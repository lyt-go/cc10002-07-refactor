"""维度版本台账接口：维度表与元数据管理共用的版本生命周期入口。

发布、下线、回滚都在这里落为同一条版本结论，并在同一事务内同步两个业务面与变更审计。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dim_version import VERSION_STATUSES, coordinator

router = APIRouter(prefix="/api/dim-version", tags=["维度版本台账"])

LIST_FIELDS = [
    "版本编号", "维度编号", "维度名称", "自然键列", "自然键值", "代理键",
    "版本号", "status", "生效日期", "失效日期", "发布时间", "来源动作",
]


@router.get("", response_model=PageResult[dict])
def list_versions(
    keyword: str | None = Query(default=None, description="按版本编号或代理键检索"),
    dimension_no: str | None = Query(default=None, description="按维度编号过滤"),
    natural_key: str | None = Query(default=None, description="按自然键列/自然键值过滤"),
    status: str | None = Query(default=None, description="草稿、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取共享版本台账；无数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in VERSION_STATUSES:
        raise HTTPException(status_code=400, detail=f"版本结论仅支持：{'、'.join(VERSION_STATUSES)}")
    items, total = coordinator.list_versions(
        keyword=keyword, dimension_no=dimension_no, natural_key=natural_key,
        status=status, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/resolve")
def resolve_version(
    dimension_no: str = Query(..., description="维度编号，如 DIM_CUSTOMER"),
    natural_key_column: str = Query(..., description="自然键列，如 customer_no"),
    natural_key_value: str = Query(..., description="自然键值"),
    on_date: str = Query(..., description="按该日期解析生效中的版本"),
) -> dict[str, Any]:
    """按日期解析版本结论：返回该日期对应的历史版本，已下线版本同样保留可追溯。"""
    items = coordinator.resolve(dimension_no, natural_key_column, natural_key_value, on_date)
    return {"on_date": on_date, "total": len(items), "items": items}


@router.get("/undo")
def list_undo(keyword: str | None = Query(default=None, description="按回滚编号或维度编号检索")) -> dict[str, Any]:
    """回滚台账：记录每次版本结论的行级快照，回滚必须按链路倒序执行。"""
    items = coordinator.list_undo(keyword=keyword)
    return {"total": len(items), "items": items}


@router.get("/export")
def export_versions() -> dict[str, Any]:
    """导出版本台账全量数据（含历史版本与已下线版本，供追溯）。"""
    items, total = coordinator.list_versions(page=1, size=10000)
    return {"module": "dim_version", "total": total, "items": items}


@router.get("/{version_id}/refs")
def version_refs(version_id: int) -> dict[str, Any]:
    """查询引用了指定版本的元数据项，支撑维度页与元数据页跨页面联动。"""
    if coordinator.get_version(version_id) is None:
        raise HTTPException(status_code=404, detail=f"维度版本 {version_id} 不存在或已归档")
    items = coordinator.refs_of_version(version_id)
    return {"version_id": version_id, "total": len(items), "items": items}


@router.get("/{version_id}", response_model=dict)
def get_version(version_id: int) -> dict:
    version = coordinator.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"维度版本 {version_id} 不存在或已归档")
    return version


@router.post("", response_model=ActionResult)
def create_draft(payload: EntryPayload) -> ActionResult:
    """登记一条维度版本草稿；与已发布版本生效区间冲突时直接拒绝，草稿不得覆盖历史。"""
    entry, message = coordinator.create_draft(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="维度版本草稿已登记", entry=entry)


@router.post("/{version_id}/actions", response_model=ActionResult)
def run_version_action(version_id: int, payload: EntryPayload) -> ActionResult:
    """对版本执行发布版本、下线版本、回滚；结论同时驱动维度表与元数据两个业务面。"""
    action = str(payload.values.get("action") or "").strip()
    operator = str(payload.values.get("operator") or "值班管理员")
    remark = payload.remark or str(payload.values.get("remark") or "")
    if action == "发布版本":
        entry, message = coordinator.publish_version(version_id, operator=operator, remark=remark)
    elif action == "下线版本":
        entry, message = coordinator.offline_version(
            version_id, operator=operator, remark=remark,
            offline_date=str(payload.values.get("下线日期") or "") or None,
        )
    elif action == "回滚":
        undo_id = payload.values.get("undo_id", version_id)
        entry, message = coordinator.rollback(int(undo_id), operator=operator, remark=remark)
    else:
        return ActionResult(ok=False, message=f"动作「{action}」不属于维度版本台账可执行范围")
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"版本结论已{action}", entry=entry)
