"""元数据管理业务规则。

重构后的职责边界：元数据项不再自行判断维度属性是否仍然有效，只允许引用一条明确的维度版本
（版本 id + 发布时冻结的代理键快照）。发布时由维度版本台账协调器校验版本结论与生效日期；
版本下线或新版本发布都不会批量改写历史引用，元数据仍指向各自日期对应的版本。动作与字段
含义保持兼容，pending/abnormal 与版本结论同口径。
"""
from __future__ import annotations

from typing import Any

from app.services.dim_version import META_MODULE, coordinator
from app.store import store

MODULE = META_MODULE
REQUIRED_FIELDS = ["元数据编号", "对象类型", "对象名称"]
STATUS_ORDER = ["草稿", "元数据评审中", "已发布", "已下线"]
ACTION_RULES = {"提交元数据评审": "元数据评审中", "发布元数据": "已发布", "下线元数据": "已下线"}
NEGATIVE_ACTIONS = ["下线元数据"]

# 元数据项可携带的维度版本引用字段（发布时冻结快照，历史引用不被批量改写）。
REFERENCE_FIELDS = [
    "引用版本id", "引用版本编号", "引用维度编号", "引用自然键列",
    "引用自然键值", "引用代理键", "引用生效日期",
]


class MetamodelService:
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
            rows = [row for row in rows if keyword in str(row.get("元数据编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        items = [coordinator.decorate_meta_entry(row) for row in rows[start:start + size]]
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return coordinator.decorate_meta_entry(entry) if entry else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for extra in ["属性名称", "属性值", "技术负责人", "业务负责人", "更新周期", *REFERENCE_FIELDS]:
            if values.get(extra) is not None:
                entry[extra] = values.get(extra)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return coordinator.decorate_meta_entry(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        values: dict[str, Any] | None = None,
        operator: str = "值班管理员",
        remark: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        # 委托给版本台账协调器：发布前解析引用版本，两个业务面在同一事务内同成同退。
        return coordinator.apply_meta_action(
            entry_id, action, values=values, operator=operator, remark=remark
        )
