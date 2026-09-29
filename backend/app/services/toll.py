"""通行费用业务规则。

过路记录的所有费用结论都由 ``toll_policy`` 统一口径给出，本服务只负责：
- 登记/补录时套用口径，并保证同一业务键的并发补录只落一条最终结论；
- 业务确认（异常必须凭凭证）、冲销等状态流转，并把每次结论写入判定历史；
- 车辆列表费用列、过路明细、费用汇总读取同一份结论，不另算第二遍。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.store import store
from app.services.toll_policy import (
    CONCLUSION_ABNORMAL,
    CONCLUSION_EXCEPTION,
    CONCLUSION_NORMAL,
    policy,
)

MODULE = "toll"
REQUIRED_FIELDS = ["车辆编号", "收费站名称", "通行方向", "收费金额", "通行日期"]
VALID_DIRECTIONS = ["进京", "出京", "双向"]
VALID_ABNORMAL_TYPES = {"金额无法识别", "金额低于阈值", "金额高于阈值", "例外规则冲突"}

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
STATUS_REVERSED = "已冲销"
STATUS_ORDER = [STATUS_PENDING, STATUS_CONFIRMED, STATUS_REVERSED]

# 补录、确认都走同一把锁：内存实现下保证同一业务键只产生一个最终结论
_write_lock = threading.RLock()


def _now() -> str:
    return date.today().isoformat()


class TollService:
    # ---------- 读取：明细 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        vehicle_id: str | None = None,
        station: str | None = None,
        direction: str | None = None,
        abnormal: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if vehicle_id:
            rows = [row for row in rows if vehicle_id in str(row.get("车辆编号", ""))]
        if station:
            rows = [row for row in rows if station in str(row.get("收费站名称", ""))]
        if direction:
            rows = [row for row in rows if str(row.get("通行方向", "")).strip() == direction.strip()]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if abnormal is not None:
            # 待核标记是统一口径现算的结论，不能读行里的静态字段
            rows = [row for row in rows if self._needs_review(row) == abnormal]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row is not None else None

    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """明细展示：规则结论现算，人工最终结论不被规则改版覆盖。"""
        presented = dict(row)
        live = policy.evaluate(
            station=str(row.get("收费站名称") or ""),
            direction=str(row.get("通行方向") or ""),
            amount_raw=row.get("收费金额"),
            on_date=row.get("通行日期"),
        )
        presented["规则结论"] = live["判定结论"]
        presented["规则依据"] = live["判定依据"]
        presented["适用规则版本"] = live["适用规则版本"]
        presented["命中例外"] = "、".join(live["命中例外"]) or ""
        # 已业务确认的记录以人工结论为准，规则结论仅作参考留痕；已冲销单列冲销
        if row.get("状态") == STATUS_REVERSED:
            presented["判定结论"] = "冲销"
        else:
            presented["判定结论"] = row.get("最终结论") or live["判定结论"]
        presented["待核"] = self._needs_review(row)
        return presented

    @staticmethod
    def _needs_review(row: dict[str, Any]) -> bool:
        if row.get("状态") == STATUS_REVERSED:
            return False
        # 规则判异且尚未业务确认的记录一律待核
        live = policy.evaluate(
            station=str(row.get("收费站名称") or ""),
            direction=str(row.get("通行方向") or ""),
            amount_raw=row.get("收费金额"),
            on_date=row.get("通行日期"),
        )
        return live["判定结论"] == CONCLUSION_ABNORMAL and not row.get("最终结论")

    # ---------- 写入：登记 / 补录 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        cleaned = {field: str(values.get(field) or "").strip() for field in REQUIRED_FIELDS}
        missing = [field for field in REQUIRED_FIELDS if not cleaned[field]]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        if cleaned["通行方向"] not in ("进京", "出京"):
            return None, "通行方向只允许「进京 / 出京」，请修正后再提交"
        try:
            amount = round(float(cleaned["收费金额"]), 2)
        except ValueError:
            return None, f"收费金额「{cleaned['收费金额']}」不是数字，请按凭证录入"

        rows = store.rows(MODULE)
        record_no = str(values.get("记录编号") or "").strip()
        business_key = self._business_key(cleaned)

        with _write_lock:
            # 并发补录：同一业务事实只允许一条记录、一个最终结论
            duplicate = self._find_by_business_key(business_key)
            if duplicate is not None:
                return None, (
                    f"同一过路事实已存在记录 {duplicate.get('记录编号')}（业务键 {business_key}），"
                    "重复补录已拦截，以已有结论为准"
                )
            if record_no and any(row.get("记录编号") == record_no for row in rows):
                return None, f"记录编号 {record_no} 已存在，请勿重复提交"

            verdict = policy.evaluate(
                station=cleaned["收费站名称"],
                direction=cleaned["通行方向"],
                amount_raw=amount,
                on_date=cleaned["通行日期"],
            )
            entry: dict[str, Any] = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "记录编号": record_no or self._next_record_no(rows),
                "车辆编号": cleaned["车辆编号"],
                "收费站名称": cleaned["收费站名称"],
                "通行方向": cleaned["通行方向"],
                "收费金额": amount,
                "通行日期": cleaned["通行日期"],
                "凭证编号": str(values.get("凭证编号") or "").strip(),
                "业务键": business_key,
                "状态": STATUS_PENDING,
                "pending": True,
                "abnormal": verdict["判定结论"] == CONCLUSION_ABNORMAL,
                "最终结论": "",
                "确认说明": "",
                "判定历史": [
                    {
                        "时间": _now(),
                        "动作": "登记",
                        "判定结论": verdict["判定结论"],
                        "异常类型": verdict["异常类型"],
                        "适用规则版本": verdict["适用规则版本"],
                        "判定依据": verdict["判定依据"],
                        "凭证编号": str(values.get("凭证编号") or "").strip(),
                    }
                ],
            }
            rows.append(entry)
        return self._present(entry), ""

    @staticmethod
    def _business_key(values: dict[str, str]) -> str:
        return (
            f"{values['车辆编号']}|{values['收费站名称']}|"
            f"{values['通行方向']}|{values['通行日期']}"
        )

    @staticmethod
    def _next_record_no(rows: list[dict[str, Any]]) -> str:
        return f"TOLL-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:04d}"

    def _find_by_business_key(self, business_key: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("业务键") == business_key:
                return row
        return None

    # ---------- 写入：业务确认 / 冲销 ----------

    def confirm_entry(
        self,
        entry_id: int,
        *,
        final_conclusion: str,
        voucher_no: str,
        note: str,
    ) -> tuple[dict[str, Any] | None, str]:
        final_conclusion = final_conclusion.strip()
        voucher_no = voucher_no.strip()
        note = note.strip()
        if final_conclusion not in (CONCLUSION_NORMAL, CONCLUSION_EXCEPTION):
            return None, "业务确认结论只允许「正常 / 例外放行」，待核记录不能直接维持异常"

        with _write_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"过路记录 {entry_id} 不存在或已归档"
            if entry.get("状态") == STATUS_REVERSED:
                return None, "已冲销记录不可再确认；如需恢复请重新补录"
            if entry.get("最终结论"):
                return None, (
                    f"该记录已业务确认为「{entry.get('最终结论')}」，只允许保留一个最终结论，"
                    "如需更改请走冲销后补录"
                )
            live = policy.evaluate(
                station=str(entry.get("收费站名称") or ""),
                direction=str(entry.get("通行方向") or ""),
                amount_raw=entry.get("收费金额"),
                on_date=entry.get("通行日期"),
            )
            if live["判定结论"] == CONCLUSION_ABNORMAL and not voucher_no:
                return None, (
                    f"口径判异（{live['异常类型']}），业务确认必须填写业务确认凭证编号，"
                    "无凭证不能给出最终结论"
                )
            entry["最终结论"] = final_conclusion
            entry["确认说明"] = note
            if voucher_no and not entry.get("凭证编号"):
                entry["凭证编号"] = voucher_no
            entry["状态"] = STATUS_CONFIRMED
            entry["pending"] = False
            entry["abnormal"] = False
            entry["判定历史"].append(
                {
                    "时间": _now(),
                    "动作": "业务确认",
                    "判定结论": final_conclusion,
                    "异常类型": "",
                    "适用规则版本": live["适用规则版本"],
                    "判定依据": (
                        f"规则口径为「{live['判定结论']}」：{live['判定依据']}；"
                        f"业务凭凭证 {voucher_no or entry.get('凭证编号') or '（沿用原凭证）'} "
                        f"确认为「{final_conclusion}」。{note}".rstrip("。")
                    ),
                    "凭证编号": voucher_no or entry.get("凭证编号", ""),
                }
            )
        return self._present(entry), "业务确认已落为唯一最终结论"

    def reverse_entry(self, entry_id: int, reason: str) -> tuple[dict[str, Any] | None, str]:
        reason = reason.strip()
        with _write_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"过路记录 {entry_id} 不存在或已归档"
            if entry.get("状态") == STATUS_REVERSED:
                return None, "该记录已冲销，不能重复冲销"
            if not reason:
                return None, "冲销必须填写冲销原因，便于费用留痕追溯"
            entry["状态"] = STATUS_REVERSED
            entry["pending"] = False
            entry["abnormal"] = False
            entry["判定历史"].append(
                {
                    "时间": _now(),
                    "动作": "冲销",
                    "判定结论": "冲销",
                    "异常类型": "",
                    "适用规则版本": next(
                        (item.get("适用规则版本") for item in reversed(entry["判定历史"])),
                        "",
                    ),
                    "判定依据": f"冲销原因：{reason}；原费用 {entry.get('收费金额')} 元不计入汇总",
                    "凭证编号": entry.get("凭证编号", ""),
                }
            )
        return self._present(entry), "过路记录已冲销，费用汇总不再计入"

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None):
        values = values or {}
        if action == "业务确认":
            return self.confirm_entry(
                entry_id,
                final_conclusion=str(values.get("最终结论") or ""),
                voucher_no=str(values.get("凭证编号") or ""),
                note=str(values.get("备注") or ""),
            )
        if action == "冲销费用":
            return self.reverse_entry(entry_id, str(values.get("冲销原因") or ""))
        return None, f"动作「{action}」不属于通行费用可执行范围"

    # ---------- 汇总：费用看板与车辆列表共用 ----------

    def summary(self, *, month: str | None = None) -> dict[str, Any]:
        """费用汇总只统计已确认金额；待核与已冲销单列，不计入合计。"""
        month = month or date.today().strftime("%Y-%m")
        confirmed_total = 0.0
        confirmed_count = 0
        pending_abnormal_count = 0
        pending_abnormal_amount = 0.0
        exception_count = 0
        reversed_count = 0
        reversed_amount = 0.0
        for row in store.rows(MODULE):
            if not str(row.get("通行日期") or "").startswith(month):
                continue
            amount = float(row.get("收费金额") or 0)
            presented = self._present(row)
            if row.get("状态") == STATUS_REVERSED:
                reversed_count += 1
                reversed_amount += amount
                continue
            if row.get("状态") == STATUS_CONFIRMED:
                confirmed_total += amount
                confirmed_count += 1
                if presented["判定结论"] == CONCLUSION_EXCEPTION:
                    exception_count += 1
                continue
            if self._needs_review(row):
                pending_abnormal_count += 1
                pending_abnormal_amount += amount
        return {
            "月份": month,
            "已确认费用": round(confirmed_total, 2),
            "已确认笔数": confirmed_count,
            "待核异常笔数": pending_abnormal_count,
            "待核异常金额": round(pending_abnormal_amount, 2),
            "例外放行笔数": exception_count,
            "已冲销笔数": reversed_count,
            "已冲销金额": round(reversed_amount, 2),
        }

    def vehicle_toll_summary(self, vehicle_id: str) -> dict[str, Any]:
        """车辆列表费用列：和明细、汇总读同一结论。"""
        month = date.today().strftime("%Y-%m")
        confirmed_total = 0.0
        pending_count = 0
        for row in store.rows(MODULE):
            if str(row.get("车辆编号") or "") != vehicle_id:
                continue
            if row.get("状态") == STATUS_CONFIRMED:
                confirmed_total += float(row.get("收费金额") or 0)
            elif (
                str(row.get("通行日期") or "").startswith(month)
                and row.get("状态") != STATUS_REVERSED
                and self._needs_review(row)
            ):
                pending_count += 1
        return {
            "累计已确认通行费": round(confirmed_total, 2),
            "本月待核通行记录": pending_count,
        }
