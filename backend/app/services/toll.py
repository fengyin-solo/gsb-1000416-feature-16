"""通行费用业务规则。

所有费用结论都由 ``toll_policy.evaluate`` 这一个口径产出：登记时快照一次，
车辆列表、过路明细、费用汇总读同一份快照字段，规则升级不会重算历史记录。

- 多规则同时命中：先按例外路线优先级（数字小者优先）裁决；仍冲突时挂待核，
  以业务确认凭证做最终结论（凭证确认后锁定，任何动作不得再改）。
- 金额异常一律标记「待核」，待核记录不能直接确认。
- 并发补录按业务唯一键去重，同一笔过路事实只允许一个最终结论。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.services import toll_policy
from app.services.toll_policy import POLICY_VERSIONS, current_policy, evaluate
from app.store import store

MODULE = "toll"
REQUIRED_FIELDS = ["车辆编号", "收费站名称", "收费金额", "通行方向", "通行日期"]
# 补录去重的业务唯一键：同一车同一天在同一站同一方向的同额缴费只认一条
DEDUP_KEY_FIELDS = ("车辆编号", "收费站名称", "通行方向", "通行日期", "收费金额")

STATUS_PENDING = "待确认"
STATUS_REVIEW = "待核"
STATUS_CONFIRMED = "已确认"
STATUS_WRITTEN_OFF = "待冲销"
STATUS_ORDER = [STATUS_PENDING, STATUS_REVIEW, STATUS_CONFIRMED, STATUS_WRITTEN_OFF]

# 终态：凭证确认或冲销之后锁定，只保留一个最终结论
FINAL_STATUSES = {STATUS_CONFIRMED, STATUS_WRITTEN_OFF}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _business_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(field) for field in DEDUP_KEY_FIELDS)


def _append_trail(entry: dict[str, Any], action: str, detail: str) -> None:
    entry.setdefault("核查轨迹", []).append({"时间": _now(), "动作": action, "说明": detail})


class TollService:
    def __init__(self) -> None:
        self._create_lock = threading.Lock()
        self._bootstrap()

    # ---- 初始化与结论快照 -------------------------------------------------

    def _bootstrap(self) -> None:
        """给历史/示例记录按当时口径补打结论快照，只补一次，之后不再重算。"""
        for entry in store.rows(MODULE):
            if "规则版本" in entry:
                continue
            paid = self._to_amount(entry.get("收费金额"))
            conclusion = evaluate(
                str(entry.get("收费站名称", "")),
                str(entry.get("通行方向", "")),
                paid if paid is not None else 0.0,
                str(entry.get("通行日期", "")),
            )
            entry.update({
                "规则版本": conclusion["规则版本"],
                "判定结论": conclusion["判定结论"],
                "异常原因": conclusion["异常原因"],
                "应收金额": conclusion["应收金额"],
                "命中例外": conclusion["命中例外"],
                "判定依据": conclusion["判定依据"],
                "入账金额": None,
                "final": False,
            })
            status = entry.get("status")
            if conclusion["异常标记"] and status != STATUS_WRITTEN_OFF:
                status = STATUS_REVIEW
            elif status not in STATUS_ORDER:
                status = STATUS_PENDING
            entry["status"] = status
            entry["记录状态"] = status
            entry["pending"] = status not in FINAL_STATUSES
            entry["abnormal"] = status == STATUS_REVIEW
            if status == STATUS_CONFIRMED:
                entry["final"] = True
                entry["入账金额"] = conclusion["应收金额"] if conclusion["应收金额"] is not None else paid
                _append_trail(entry, "历史确认", f"按{conclusion['规则版本']}口径确认，结论冻结")
            elif status == STATUS_WRITTEN_OFF:
                entry["final"] = True
                _append_trail(entry, "历史冲销", "冲销结论冻结")
            else:
                _append_trail(entry, "补录登记", f"按{conclusion['规则版本']}口径生成初始结论")

    @staticmethod
    def _to_amount(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return round(amount, 2)

    # ---- 查询 -------------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        station: str | None = None,
        vehicle: str | None = None,
        review: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("记录编号", "")) or keyword in str(row.get("车辆编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if station:
            rows = [row for row in rows if station in str(row.get("收费站名称", ""))]
        if vehicle:
            rows = [row for row in rows if vehicle in str(row.get("车辆编号", ""))]
        if review is not None:
            rows = [row for row in rows if bool(row.get("abnormal")) == review]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ---- 登记与补录（并发去重，只留一个最终结论） --------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        cleaned: dict[str, Any] = {}
        missing: list[str] = []
        for field in REQUIRED_FIELDS:
            raw = values.get(field)
            if field == "收费金额":
                amount = self._to_amount(raw)
                if amount is None:
                    missing.append(field)
                else:
                    cleaned[field] = amount
                continue
            if not str(raw or "").strip():
                missing.append(field)
            else:
                cleaned[field] = str(raw).strip()
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"

        with self._create_lock:
            rows = store.rows(MODULE)
            candidate_key = tuple(cleaned.get(field) for field in DEDUP_KEY_FIELDS)
            for existing in rows:
                if _business_key(existing) == candidate_key:
                    tip = "已有最终结论，禁止重复补录" if existing.get("final") else "正在处理中，请勿重复补录"
                    return existing, f"该过路事实已存在记录 {existing.get('记录编号')}（{tip}），只保留一个结论"

            conclusion = evaluate(
                cleaned["收费站名称"],
                cleaned["通行方向"],
                cleaned["收费金额"],
                cleaned["通行日期"],
            )
            new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            abnormal = conclusion["判定结论"] == "待核"
            entry: dict[str, Any] = {
                "id": new_id,
                "记录编号": f"TOLL-{new_id:04d}",
                **cleaned,
                "凭证编号": str(values.get("凭证编号") or "").strip(),
                "status": STATUS_REVIEW if abnormal else STATUS_PENDING,
                "记录状态": STATUS_REVIEW if abnormal else STATUS_PENDING,
                "pending": True,
                "abnormal": abnormal,
                "final": False,
                "规则版本": conclusion["规则版本"],
                "判定结论": conclusion["判定结论"],
                "异常原因": conclusion["异常原因"],
                "应收金额": conclusion["应收金额"],
                "命中例外": conclusion["命中例外"],
                "判定依据": conclusion["判定依据"],
                "入账金额": None,
                "核查轨迹": [],
            }
            _append_trail(entry, "补录登记", f"按{conclusion['规则版本']}口径生成初始结论：{conclusion['判定结论']}")
            rows.append(entry)
            return entry, "过路记录已补录" + ("，费用异常已标记待核" if abnormal else "")

    # ---- 动作流转 ---------------------------------------------------------

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"过路记录 {entry_id} 不存在或已归档"
        values = values or {}

        if action == "确认费用":
            return self._confirm(entry)
        if action == "凭证确认":
            return self._confirm_by_voucher(entry, values)
        if action == "冲销费用":
            return self._write_off(entry, values)
        return None, f"动作「{action}」不属于通行费用可执行范围"

    def _confirm(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if entry.get("final"):
            return None, f"{entry.get('记录编号')} 已有最终结论并锁定，不能重复确认"
        if entry["status"] == STATUS_REVIEW:
            return None, "费用异常待核，不能直接确认；请凭业务确认凭证执行「凭证确认」"
        if entry["status"] != STATUS_PENDING:
            return None, f"当前状态「{entry['status']}」不允许确认费用"
        entry["status"] = STATUS_CONFIRMED
        entry["记录状态"] = STATUS_CONFIRMED
        entry["pending"] = False
        entry["final"] = True
        entry["入账金额"] = entry.get("应收金额")
        entry["判定依据"] = entry["判定依据"] + [f"费用确认：按规则版本 {entry['规则版本']} 的应收口径入账"]
        _append_trail(entry, "确认费用", f"按{entry['规则版本']}口径确认，结论冻结")
        return entry, "过路记录已确认费用并锁定"

    def _confirm_by_voucher(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("final"):
            return None, f"{entry.get('记录编号')} 已有最终结论并锁定，凭证不能覆盖"
        voucher_no = str(values.get("凭证编号") or entry.get("凭证编号") or "").strip()
        if not voucher_no:
            return None, "凭证确认必须填写业务确认凭证编号，否则最终结论无依据"
        voucher_amount = self._to_amount(values.get("确认金额"))
        if voucher_amount is None:
            voucher_amount = self._to_amount(entry.get("收费金额"))
        entry["凭证编号"] = voucher_no
        entry["判定结论"] = "正常"
        entry["异常原因"] = None
        entry["应收金额"] = voucher_amount
        entry["入账金额"] = voucher_amount
        entry["命中例外"] = entry.get("命中例外", [])
        entry["判定依据"] = entry["判定依据"] + [
            f"业务确认凭证 {voucher_no} 裁决：按{voucher_amount:g}元入账，凭证结论优先并锁定"
        ]
        entry["status"] = STATUS_CONFIRMED
        entry["记录状态"] = STATUS_CONFIRMED
        entry["pending"] = False
        entry["abnormal"] = False
        entry["final"] = True
        _append_trail(entry, "凭证确认", f"凭证{voucher_no}裁决为最终结论，入账{voucher_amount:g}元")
        return entry, "已按业务确认凭证形成唯一最终结论并锁定"

    def _write_off(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("final"):
            return None, f"{entry.get('记录编号')} 已有最终结论并锁定，不能再冲销"
        reason = str(values.get("冲销原因") or "").strip()
        entry["status"] = STATUS_WRITTEN_OFF
        entry["记录状态"] = STATUS_WRITTEN_OFF
        entry["pending"] = False
        entry["final"] = True
        detail = "冲销并锁定，费用不计入汇总"
        if reason:
            detail += f"：{reason}"
        entry["判定依据"] = entry["判定依据"] + [f"冲销费用：{detail}"]
        _append_trail(entry, "冲销费用", detail)
        return entry, "过路记录已冲销并锁定"

    # ---- 统一口径输出：车辆 / 汇总 / 规则版本 ------------------------------

    def summary(self, vehicle: str | None = None) -> dict[str, Any]:
        """费用汇总：与车辆列表、过路明细读同一结论字段，口径只有这一份。"""
        rows = store.rows(MODULE)
        if vehicle:
            rows = [row for row in rows if vehicle in str(row.get("车辆编号", ""))]

        by_vehicle_map: dict[str, dict[str, Any]] = {}
        confirmed_total = 0.0
        review_total = 0.0
        written_off_total = 0.0
        review_count = 0

        for row in rows:
            vehicle_no = str(row.get("车辆编号", ""))
            bucket = by_vehicle_map.setdefault(vehicle_no, {
                "车辆编号": vehicle_no,
                "过路记录数": 0,
                "正常记录": 0,
                "待核记录": 0,
                "已确认金额": 0.0,
                "待核金额": 0.0,
                "冲销金额": 0.0,
            })
            paid = self._to_amount(row.get("收费金额")) or 0.0
            booked = self._to_amount(row.get("入账金额"))
            bucket["过路记录数"] += 1

            if row.get("status") == STATUS_REVIEW:
                bucket["待核记录"] += 1
                bucket["待核金额"] = round(bucket["待核金额"] + paid, 2)
                review_total = round(review_total + paid, 2)
                review_count += 1
            elif row.get("status") == STATUS_WRITTEN_OFF:
                bucket["冲销金额"] = round(bucket["冲销金额"] + paid, 2)
                written_off_total = round(written_off_total + paid, 2)
            else:
                bucket["正常记录"] += 1
                if row.get("status") == STATUS_CONFIRMED and booked is not None:
                    bucket["已确认金额"] = round(bucket["已确认金额"] + booked, 2)
                    confirmed_total = round(confirmed_total + booked, 2)

        by_vehicle = [by_vehicle_map[key] for key in sorted(by_vehicle_map)]
        cards = [
            {"label": "已确认通行费", "value": confirmed_total},
            {"label": "待核通行费", "value": review_total},
            {"label": "待核记录", "value": review_count},
            {"label": "本期冲销", "value": written_off_total},
        ]
        policy = current_policy()
        return {
            "cards": cards,
            "by_vehicle": by_vehicle,
            "policy_version": policy.version,
        }

    def vehicle_toll_map(self) -> dict[str, dict[str, Any]]:
        """车辆列表注入通行费用结论用，数据来源与费用汇总完全一致。"""
        return {item["车辆编号"]: item for item in self.summary()["by_vehicle"]}

    def policy_overview(self) -> dict[str, Any]:
        """对外暴露统一口径：金额阈值、例外路线、优先级与生效区间。"""
        active = current_policy()
        return {
            "current_version": active.version,
            "statuses": STATUS_ORDER,
            "dedup_key": list(DEDUP_KEY_FIELDS),
            "resolution": "多规则命中先按例外路线优先级裁决；仍冲突时挂待核，以业务确认凭证为最终结论",
            "versions": [version.describe() for version in sorted(POLICY_VERSIONS, key=lambda item: item.effective_from)],
        }


toll_service = TollService()
