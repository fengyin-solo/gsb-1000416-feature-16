"""车辆调度业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.toll import TollService

MODULE = "vehicle"
# 车辆列表上的通行费列直接复用通行费用服务，保证和过路明细、费用汇总同一结论
toll_service = TollService()
REQUIRED_FIELDS = ["车辆编号", "车牌号", "车型类别"]
STATUS_ORDER = ["空闲", "已派单", "执行中", "维修中", "停运"]
ACTION_RULES = {"派发出车": "已派单", "收车归队": "空闲", "报修车辆": "维修中"}
NEGATIVE_ACTIONS = []


class VehicleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("车辆编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self._with_toll(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_toll(entry) if entry is not None else None

    def _with_toll(self, row: dict[str, Any]) -> dict[str, Any]:
        """附加通行费结论：数据仍由 toll 服务统一计算，本模块不另立口径。"""
        summary = toll_service.vehicle_toll_summary(str(row.get("车辆编号") or ""))
        enriched = dict(row)
        enriched.update(summary)
        return enriched

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"冷藏车辆 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于车辆调度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"冷藏车辆已{action}"
