"""车辆调度业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.toll import toll_service
from app.store import store

MODULE = "vehicle"
REQUIRED_FIELDS = ["车辆编号", "车牌号", "车型类别"]
STATUS_ORDER = ["空闲", "已派单", "执行中", "维修中", "停运"]
ACTION_RULES = {"派发出车": "已派单", "收车归队": "空闲", "报修车辆": "维修中"}
NEGATIVE_ACTIONS = []

# 车辆列表展示的通行费用列，数值直接取费用汇总的同一结论
TOLL_FIELDS = ["通行费合计", "待核通行费", "待核记录数"]


def _attach_toll(row: dict[str, Any], toll_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把费用汇总按车辆的结论挂到车辆记录上，保证车辆列表与费用汇总同源。"""
    bucket = toll_map.get(str(row.get("车辆编号", "")))
    row["通行费合计"] = bucket["已确认金额"] if bucket else 0.0
    row["待核通行费"] = bucket["待核金额"] if bucket else 0.0
    row["待核记录数"] = bucket["待核记录"] if bucket else 0
    return row


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
        toll_map = toll_service.vehicle_toll_map()
        rows = [_attach_toll(row, toll_map) for row in rows]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return _attach_toll(entry, toll_service.vehicle_toll_map())

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
