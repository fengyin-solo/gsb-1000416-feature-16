"""通行费用接口：过路记录、统一口径、费用汇总都从同一份结论读取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.toll import STATUS_ORDER, toll_service

router = APIRouter(prefix="/api/toll", tags=["通行费用"])

LIST_FIELDS = ["记录编号", "车辆编号", "收费站名称", "通行方向", "收费金额", "应收金额", "通行日期", "凭证编号", "判定结论", "记录状态"]


@router.get("/policy")
def get_policy() -> dict[str, Any]:
    """统一判定口径：金额阈值、例外路线、优先级与历史版本集中在这里查看。"""
    return toll_service.policy_overview()


@router.get("/summary")
def get_summary(vehicle: str | None = Query(default=None, description="按车辆编号过滤")) -> dict[str, Any]:
    """费用汇总：按车辆聚合已确认、待核、冲销金额，与车辆列表、过路明细同源。"""
    return toll_service.summary(vehicle=vehicle)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号或车辆编号检索"),
    status: str | None = Query(default=None, description="待确认、待核、已确认、待冲销"),
    station: str | None = Query(default=None, description="按收费站名称检索"),
    vehicle: str | None = Query(default=None, description="按车辆编号检索"),
    review: bool | None = Query(default=None, description="仅看待核 / 排除待核"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按收费站、方向、车辆、待核标记等条件过滤过路记录；空页不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"状态需为：{'、'.join(STATUS_ORDER)}")
    items, total = toll_service.list_entries(
        keyword=keyword, status=status, station=station, vehicle=vehicle, review=review, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出通行费用清单：返回当前全部过路记录及其统一结论快照。"""
    items, total = toll_service.list_entries(page=1, size=10000)
    return {"module": "toll", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条过路记录明细（含判定依据与核查轨迹）；不存在时给出可读说明。"""
    entry = toll_service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"过路记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记（含并发补录）一条过路记录；按业务唯一键去重，只保留一个结论。"""
    entry, message = toll_service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    duplicated = message.startswith("该过路事实已存在")
    return ActionResult(ok=not duplicated, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """确认费用、凭证确认（裁决待核）、冲销费用；终态锁定，不允许重复结论。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = toll_service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
