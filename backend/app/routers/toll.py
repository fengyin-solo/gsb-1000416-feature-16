"""通行费用接口：过路记录登记/补录、统一口径判定、业务确认、冲销与费用汇总。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.toll import STATUS_ORDER, TollService
from app.services.toll_policy import policy

router = APIRouter(prefix="/api/toll", tags=["通行费用"])

service = TollService()

LIST_FIELDS = [
    "记录编号", "车辆编号", "收费站名称", "收费金额", "通行方向",
    "通行日期", "凭证编号", "判定结论", "异常类型", "适用规则版本", "状态",
]


@router.get("/rules")
def list_rules() -> dict[str, Any]:
    """统一口径：金额阈值版本 + 例外路线（按优先级），供页面直接展示。"""
    return {
        "阈值版本": policy.versions(),
        "当前生效版本": policy.current_version(),
        "例外路线": sorted(
            policy.exception_rules(), key=lambda item: int(item.get("优先级", 0)), reverse=True
        ),
    }


@router.get("/summary")
def fee_summary(month: str | None = Query(default=None, description="YYYY-MM，默认本月")) -> dict[str, Any]:
    """费用汇总看板：已确认合计、待核异常、例外放行、已冲销分开列示。"""
    return service.summary(month=month)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    vehicle_id: str | None = Query(default=None, description="按车辆编号过滤"),
    station: str | None = Query(default=None, description="按收费站名称过滤"),
    direction: str | None = Query(default=None, description="进京 / 出京"),
    status: str | None = Query(default=None, description="待确认 / 已确认 / 已冲销"),
    abnormal: str | None = Query(default=None, description="只看异常待核记录：true / false"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """过路明细：结论、依据、适用版本全部来自统一口径服务。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"记录状态只允许：{'、'.join(STATUS_ORDER)}")
    abnormal_flag: bool | None
    if abnormal is None or abnormal == "":
        abnormal_flag = None
    elif abnormal in ("true", "1", "是"):
        abnormal_flag = True
    elif abnormal in ("false", "0", "否"):
        abnormal_flag = False
    else:
        raise HTTPException(status_code=400, detail="abnormal 只接受 true / false")
    items, total = service.list_entries(
        keyword=keyword,
        vehicle_id=vehicle_id,
        station=station,
        direction=direction,
        status=status,
        abnormal=abnormal_flag,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记/补录一条过路记录；同一业务事实重复补录会被拦截。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"过路记录已登记，口径结论：{entry['判定结论']}", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """业务确认（异常必须带凭证）与冲销；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出通行费用清单：全量明细 + 统一口径快照。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "toll", "total": total, "items": items, "summary": service.summary()}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条过路记录明细（含判定历史）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"过路记录 {entry_id} 不存在或已归档")
    return entry
