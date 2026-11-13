"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

维度版本重构后，仓库额外承担两件事：
1. 提供整库快照/恢复能力，让维度版本协调器可以把「维度表 + 元数据 + 版本台账 + 审计」
   包进同一个事务，任一步失败就整体退回原状态；
2. 统一发放自增主键，避免各业务模块各自算出重复 id。
"""
from __future__ import annotations

import copy
from typing import Any

from app.seed import SEED_ROWS

# 内部台账表：由维度版本协调器维护，不直接出现在运营概览的业务模块清单里。
INTERNAL_MODULES = {"dim_version", "dim_version_undo"}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in INTERNAL_MODULES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """整库深拷贝快照，用于事务失败时整体回滚。"""
        return {name: copy.deepcopy(rows) for name, rows in self._tables.items()}

    def restore(self, snapshot: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = {name: copy.deepcopy(rows) for name, rows in snapshot.items()}

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
