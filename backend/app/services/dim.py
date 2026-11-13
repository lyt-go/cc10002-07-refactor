"""维度层业务规则。

重构后的职责边界：维度表只负责登记自然键列、代理键列、历史策略与生效/失效区间等建模信息，
版本结论（草稿/已发布/已下线、pending/abnormal、回滚）统一由维度版本台账协调器
``app.services.dim_version`` 给出。本服务保留原有动作与字段含义，仅把动作委托给协调器，
保证维度表与元数据管理共用同一套版本判断。
"""
from __future__ import annotations

from typing import Any

from app.services.dim_version import DIM_MODULE, coordinator
from app.store import store

MODULE = DIM_MODULE
REQUIRED_FIELDS = ["维度编号", "维度名称", "自然键列"]
STATUS_ORDER = ["草稿", "建表审批中", "已上线", "已下线"]
ACTION_RULES = {"提交建表审批": "建表审批中", "确认上线": "已上线", "下线表": "已下线"}
NEGATIVE_ACTIONS = ["下线表"]


class DimService:
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
            rows = [row for row in rows if keyword in str(row.get("维度编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        items = [coordinator.decorate_dim_entry(row) for row in rows[start:start + size]]
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return coordinator.decorate_dim_entry(entry) if entry else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 保持既有登记字段兼容：代理键列、历史策略、生效/失效日期、刷新周期原样落表。
        for extra in ["代理键列", "历史策略", "生效日期", "失效日期", "刷新周期"]:
            if values.get(extra) is not None:
                entry[extra] = values.get(extra)
        entry["status"] = STATUS_ORDER[0]
        # pending/abnormal 统一由版本台账结论推导；新登记的表先按本地状态给初始口径。
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return coordinator.decorate_dim_entry(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        operator: str = "值班管理员",
        remark: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        # 委托给版本台账协调器：同一事务内同步版本结论、元数据状态与变更审计。
        return coordinator.apply_dim_action(entry_id, action, operator=operator, remark=remark)
