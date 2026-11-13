"""维度版本生命周期协调器（维度表与元数据管理共用的版本台账）。

职责边界（重构后唯一口径）：

* 维度表（dim）只登记自然键、代理键、生效/失效区间等建模信息，不再自行判断版本结论；
* 元数据项（metamodel）只允许引用一条「明确的维度版本」，发布时按引用日期解析版本结论；
* 发布、下线、历史保留、回滚全部由本协调器写出同一条版本结论，并同步驱动两个业务面的
  pending / abnormal 状态，任何一次写入都包在整库快照事务里：两个业务面同时成功，或同时
  退回原状态；
* 每次结论都追加变更审计（auditlog）与回滚台账（dim_version_undo），回滚时逆向恢复行级
  快照并再次写审计，绝不允许只改一侧状态。

历史保留原则：已发布版本（含已闭合的 SCD2 历史版本与已下线版本）的区间与代理键只追加、
不改写；元数据项在发布时冻结所引用版本的代理键快照，下线当前版本不会把历史引用批量指向
最新版本。
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

from app.store import store

VERSION_MODULE = "dim_version"
UNDO_MODULE = "dim_version_undo"
DIM_MODULE = "dim"
META_MODULE = "metamodel"
AUDIT_MODULE = "auditlog"

# 版本结论：草稿未发布、已发布（含已闭合的历史版本）、已下线（保留可追溯）。
VERSION_STATUSES = ["草稿", "已发布", "已下线"]
LEDGER_ACTIONS = ["发布版本", "下线版本", "回滚"]
DEFAULT_OPERATOR = "值班管理员"

REQUIRED_DRAFT_FIELDS = ["维度编号", "自然键列", "自然键值", "代理键", "生效日期"]


class LifecycleError(Exception):
    """版本生命周期校验失败：消息可直接返回给前端。"""


@contextmanager
def transaction() -> Iterator[None]:
    """整库快照事务：块内任意异常都恢复到进入前的状态，保证两个业务面同成同退。"""
    snapshot = store.snapshot()
    try:
        yield
    except Exception:
        store.restore(snapshot)
        raise


def _today() -> str:
    return date.today().isoformat()


def _open_end(end: Any) -> str:
    return str(end) if end not in (None, "") else "9999-12-31"


def _overlaps(start_a: str, end_a: Any, start_b: str, end_b: Any) -> bool:
    """闭区间/半开区间重叠判断，None 或空串表示至今开放。"""
    return start_a <= _open_end(end_b) and start_b <= _open_end(end_a)


class DimVersionService:
    """版本台账的查询、发布/下线结论、回滚与两个业务面的状态同步。"""

    # ---------------------------------------------------------------- 读取
    def list_versions(
        self,
        *,
        keyword: str | None = None,
        dimension_no: str | None = None,
        natural_key: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(VERSION_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("版本编号", ""))
                    or keyword in str(row.get("代理键", ""))]
        if dimension_no:
            rows = [row for row in rows if dimension_no in str(row.get("维度编号", ""))]
        if natural_key:
            rows = [row for row in rows
                    if natural_key in str(row.get("自然键列", ""))
                    or natural_key in str(row.get("自然键值", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate_version(row) for row in rows[start:start + size]], total

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        row = store.find(VERSION_MODULE, version_id)
        return self._decorate_version(row) if row else None

    def list_undo(self, *, keyword: str | None = None) -> list[dict[str, Any]]:
        rows = sorted(store.rows(UNDO_MODULE), key=lambda row: int(row.get("id", 0)), reverse=True)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("回滚编号", ""))
                    or keyword in str(row.get("维度编号", ""))]
        return [dict(row) for row in rows]

    def resolve(
        self,
        dimension_no: str,
        natural_key_column: str,
        natural_key_value: str,
        on_date: str,
    ) -> list[dict[str, Any]]:
        """按日期解析版本结论：只返回已发布/已下线的历史版本，草稿永远不参与解析。

        已下线版本同样保留在结果里供追溯——下线只代表当前不可用，不代表历史未发生。
        """
        result = []
        for row in store.rows(VERSION_MODULE):
            if row.get("status") == "草稿":
                continue
            if (str(row.get("维度编号", "")) != dimension_no
                    or str(row.get("自然键列", "")) != natural_key_column
                    or str(row.get("自然键值", "")) != natural_key_value):
                continue
            if row["生效日期"] <= on_date <= _open_end(row.get("失效日期")):
                result.append(self._decorate_version(row))
        return sorted(result, key=lambda row: row["生效日期"])

    def refs_of_version(self, version_id: int) -> list[dict[str, Any]]:
        """哪些元数据项引用了指定版本，供维度页与元数据页跨页面联动。"""
        refs = []
        for row in store.rows(META_MODULE):
            if int(row.get("引用版本id") or 0) == version_id:
                refs.append(self.decorate_meta_entry(row))
        return refs

    # ---------------------------------------------------------------- 写入
    def create_draft(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_DRAFT_FIELDS
                   if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        try:
            with transaction():
                self._assert_no_interval_conflict(
                    dimension_no=str(values["维度编号"]).strip(),
                    nk_column=str(values["自然键列"]).strip(),
                    nk_value=str(values["自然键值"]).strip(),
                    effective=str(values["生效日期"]).strip(),
                    expiry=str(values.get("失效日期") or "").strip() or None,
                    ignore_id=None,
                )
                rows = store.rows(VERSION_MODULE)
                new_id = store.next_id(VERSION_MODULE)
                dim_entry = self._find_dim_entry(str(values["维度编号"]).strip())
                version = {
                    "id": new_id,
                    "版本编号": f"DV-{new_id:04d}",
                    "status": "草稿",
                    "pending": True,
                    "abnormal": False,
                    "维度编号": str(values["维度编号"]).strip(),
                    "维度名称": str(values.get("维度名称")
                                    or (dim_entry or {}).get("维度名称") or "").strip(),
                    "自然键列": str(values["自然键列"]).strip(),
                    "自然键值": str(values["自然键值"]).strip(),
                    "代理键": str(values["代理键"]).strip(),
                    "历史策略": str(values.get("历史策略")
                                    or (dim_entry or {}).get("历史策略") or "SCD2").strip(),
                    "生效日期": str(values["生效日期"]).strip(),
                    "失效日期": str(values.get("失效日期") or "").strip() or None,
                    "版本号": max((int(r.get("版本号") or 0) for r in rows
                                   if r.get("维度编号") == str(values["维度编号"]).strip()),
                                  default=0) + 1,
                    "发布时间": None,
                    "来源动作": "登记草稿",
                }
                rows.append(version)
                if dim_entry is not None:
                    self.recompute_dim_entry(dim_entry)
                self._append_audit(
                    table="维度版本台账",
                    change_type="登记草稿",
                    before={},
                    after=copy.deepcopy(version),
                    operator=str(values.get("operator") or DEFAULT_OPERATOR),
                    reason=str(values.get("remark") or ""),
                )
                return self._decorate_version(version), ""
        except LifecycleError as exc:
            return None, str(exc)

    def publish_version(
        self, version_id: int, operator: str = DEFAULT_OPERATOR, remark: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            with transaction():
                version = store.find(VERSION_MODULE, version_id)
                if version is None:
                    raise LifecycleError(f"维度版本 {version_id} 不存在或已归档")
                if version["status"] != "草稿":
                    raise LifecycleError(
                        f"版本 {version['版本编号']} 当前结论为「{version['status']}」，只有草稿才能发布"
                    )
                # 发布前再做一次冲突判定：已发布版本优先，草稿不得覆盖任何历史区间。
                self._assert_no_interval_conflict(
                    dimension_no=version["维度编号"],
                    nk_column=version["自然键列"],
                    nk_value=version["自然键值"],
                    effective=version["生效日期"],
                    expiry=version.get("失效日期"),
                    ignore_id=version_id,
                )
                before_map = self._capture([VERSION_MODULE, DIM_MODULE, META_MODULE])

                closed = []
                for older in store.rows(VERSION_MODULE):
                    if (older["id"] == version_id
                            or older.get("status") != "已发布"
                            or older.get("失效日期")):
                        continue
                    if (older["维度编号"] == version["维度编号"]
                            and older["自然键列"] == version["自然键列"]
                            and older["自然键值"] == version["自然键值"]):
                        # SCD2 闭合旧版本：旧版本保留为历史，不删除、不改写其代理键。
                        older["失效日期"] = version["生效日期"]
                        closed.append(older)
                version["status"] = "已发布"
                version["pending"] = False
                version["abnormal"] = False
                version["失效日期"] = None
                version["发布时间"] = _today()
                version["来源动作"] = "发布版本"

                dim_entry = self._find_dim_entry(version["维度编号"])
                if dim_entry is not None:
                    # 同一条版本结论驱动维度表面：进入已上线并对齐当前生效区间。
                    dim_entry["status"] = "已上线"
                    self.recompute_dim_entry(dim_entry)
                # 结论变化后统一重算全部元数据引用口径（历史快照字段本身不被改写）。
                for meta in store.rows(META_MODULE):
                    self.recompute_meta_entry(meta)

                after_map = self._capture([VERSION_MODULE, DIM_MODULE, META_MODULE])
                self._append_audit(
                    table="维度版本台账",
                    change_type="发布版本",
                    before={"版本": self._before_version(before_map, version_id),
                            "闭合旧版本": [copy.deepcopy(r) for r in closed]},
                    after={"版本": copy.deepcopy(version),
                           "闭合旧版本编号": [r["版本编号"] for r in closed]},
                    operator=operator,
                    reason=remark,
                )
                self._append_journal(
                    action="发布版本",
                    version=version,
                    operator=operator,
                    reason=remark,
                    before_map=before_map,
                    after_map=after_map,
                )
                return self._decorate_version(version), ""
        except LifecycleError as exc:
            return None, str(exc)

    def offline_version(
        self,
        version_id: int,
        operator: str = DEFAULT_OPERATOR,
        remark: str = "",
        offline_date: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            with transaction():
                version = store.find(VERSION_MODULE, version_id)
                if version is None:
                    raise LifecycleError(f"维度版本 {version_id} 不存在或已归档")
                if version["status"] == "草稿":
                    raise LifecycleError("草稿尚未发布，不能下线；可继续编辑或放弃该草稿")
                if version["status"] == "已下线":
                    raise LifecycleError(f"版本 {version['版本编号']} 已下线，不能重复下线")
                if version.get("失效日期"):
                    raise LifecycleError(
                        f"版本 {version['版本编号']} 是已闭合的历史版本，历史保留不可改写"
                    )
                end_date = offline_date or _today()
                if end_date < version["生效日期"]:
                    raise LifecycleError("下线日期不能早于版本生效日期")

                before_map = self._capture([VERSION_MODULE, DIM_MODULE, META_MODULE])
                before_version = copy.deepcopy(version)
                # 下线只闭合当前版本：历史区间原样保留，代理键不变，便于日后追溯。
                version["status"] = "已下线"
                version["pending"] = False
                version["abnormal"] = True
                version["失效日期"] = end_date
                version["来源动作"] = "下线版本"

                dim_entry = self._find_dim_entry(version["维度编号"])
                if dim_entry is not None:
                    open_published = any(
                        r.get("status") == "已发布" and not r.get("失效日期")
                        and r.get("维度编号") == version["维度编号"]
                        for r in store.rows(VERSION_MODULE)
                    )
                    if not open_published:
                        dim_entry["status"] = "已下线"
                    self.recompute_dim_entry(dim_entry)
                for meta in store.rows(META_MODULE):
                    self.recompute_meta_entry(meta)

                after_map = self._capture([VERSION_MODULE, DIM_MODULE, META_MODULE])
                self._append_audit(
                    table="维度版本台账",
                    change_type="下线版本",
                    before=before_version,
                    after=copy.deepcopy(version),
                    operator=operator,
                    reason=remark,
                )
                self._append_journal(
                    action="下线版本",
                    version=version,
                    operator=operator,
                    reason=remark,
                    before_map=before_map,
                    after_map=after_map,
                )
                return self._decorate_version(version), ""
        except LifecycleError as exc:
            return None, str(exc)

    def rollback(
        self, undo_id: int, operator: str = DEFAULT_OPERATOR, remark: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            with transaction():
                journal = store.find(UNDO_MODULE, undo_id)
                if journal is None:
                    raise LifecycleError(f"回滚台账 {undo_id} 不存在")
                if journal.get("已回滚"):
                    raise LifecycleError(f"回滚记录 {journal['回滚编号']} 已执行过，不能重复回滚")
                chain = [r for r in store.rows(UNDO_MODULE)
                         if r.get("链路键") == journal["链路键"]]
                latest = max(chain, key=lambda r: int(r["id"]))
                if int(latest["id"]) != undo_id:
                    raise LifecycleError(
                        f"该链路之后还有「{latest['关联动作']}」未回滚，请按时间倒序依次回滚"
                    )

                snapshot = journal["快照"]["before"]
                for module, rows in snapshot.items():
                    for saved in rows:
                        target = store.find(module, int(saved["id"]))
                        if target is None:
                            raise LifecycleError(
                                f"回滚失败：{module} 中 id={saved['id']} 的记录已不存在"
                            )
                        target.clear()
                        target.update(copy.deepcopy(saved))
                journal["已回滚"] = True
                journal["status"] = "已复核"

                # 回滚后重算两个业务面的结论标志，保持台账与业务面永远同一口径。
                for dim in store.rows(DIM_MODULE):
                    self.recompute_dim_entry(dim)
                for meta in store.rows(META_MODULE):
                    self.recompute_meta_entry(meta)

                self._append_audit(
                    table="维度版本台账",
                    change_type="回滚",
                    before=copy.deepcopy(journal["快照"].get("after", {})),
                    after=copy.deepcopy(snapshot),
                    operator=operator,
                    reason=remark or f"回滚 {journal['回滚编号']}（{journal['关联动作']}）",
                )
                version = (store.find(VERSION_MODULE, int(journal["关联版本id"]))
                           if journal.get("关联版本id") else None)
                return (self._decorate_version(version) if version else dict(journal)), ""
        except LifecycleError as exc:
            return None, str(exc)

    # ------------------------------------------------- 维度表/元数据动作委托
    def apply_dim_action(
        self, entry_id: int, action: str, operator: str = DEFAULT_OPERATOR, remark: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        from app.services.dim import ACTION_RULES, STATUS_ORDER

        try:
            with transaction():
                entry = store.find(DIM_MODULE, entry_id)
                if entry is None:
                    raise LifecycleError(f"维度表 {entry_id} 不存在或已归档")
                if action not in ACTION_RULES:
                    raise LifecycleError(f"动作「{action}」不属于维度层可执行范围")
                target = ACTION_RULES[action]
                if target not in STATUS_ORDER:
                    raise LifecycleError(f"目标状态「{target}」不在允许的状态序列里")
                if entry.get("status") == target:
                    return self.decorate_dim_entry(entry), f"维度表已是「{target}」，无需重复提交"

                before_map = self._capture([DIM_MODULE, VERSION_MODULE, META_MODULE])
                before_entry = copy.deepcopy(entry)
                versions = [r for r in store.rows(VERSION_MODULE)
                            if r.get("维度编号") == entry.get("维度编号")]

                open_published = [r for r in versions
                                  if r.get("status") == "已发布" and not r.get("失效日期")]
                if action == "确认上线" and not open_published:
                    raise LifecycleError(
                        "该维度表尚无生效中的已发布版本，请先在维度版本台账发布版本后再上线"
                    )
                if action == "下线表" and versions:
                    # 表级下线由同一条版本结论驱动：逐个下线仍开放发布的版本，历史版本不动。
                    for version in [r for r in versions
                                    if r.get("status") == "已发布" and not r.get("失效日期")]:
                        version["status"] = "已下线"
                        version["abnormal"] = True
                        version["失效日期"] = _today()
                        version["来源动作"] = "下线表联动"
                    for meta in store.rows(META_MODULE):
                        self.recompute_meta_entry(meta)

                entry["status"] = target
                self.recompute_dim_entry(entry)
                after_map = self._capture([DIM_MODULE, VERSION_MODULE, META_MODULE])
                self._append_audit(
                    table="维度表",
                    change_type=action,
                    before=before_entry,
                    after=copy.deepcopy(entry),
                    operator=operator,
                    reason=remark,
                )
                self._append_journal(
                    action=action,
                    version=versions[0] if versions else None,
                    operator=operator,
                    reason=remark,
                    before_map=before_map,
                    after_map=after_map,
                    fallback_chain=f"dim:{entry_id}",
                    dim_no=str(entry.get("维度编号", "")),
                )
                return self.decorate_dim_entry(entry), f"维度表已{action}"
        except LifecycleError as exc:
            return None, str(exc)

    def apply_meta_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        operator: str = DEFAULT_OPERATOR,
        remark: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        from app.services.metamodel import ACTION_RULES, STATUS_ORDER

        values = values or {}
        try:
            with transaction():
                entry = store.find(META_MODULE, entry_id)
                if entry is None:
                    raise LifecycleError(f"元数据项 {entry_id} 不存在或已归档")
                if action not in ACTION_RULES:
                    raise LifecycleError(f"动作「{action}」不属于元数据管理可执行范围")
                target = ACTION_RULES[action]
                if target not in STATUS_ORDER:
                    raise LifecycleError(f"目标状态「{target}」不在允许的状态序列里")
                if entry.get("status") == target:
                    return self.decorate_meta_entry(entry), f"元数据项已是「{target}」，无需重复提交"

                before_map = self._capture([META_MODULE, DIM_MODULE, VERSION_MODULE])
                before_entry = copy.deepcopy(entry)

                if action == "发布元数据":
                    version_id = values.get("引用版本id", entry.get("引用版本id"))
                    on_date = str(values.get("引用生效日期")
                                  or entry.get("引用生效日期") or _today())
                    if version_id not in (None, ""):
                        vid = int(version_id)
                        version = store.find(VERSION_MODULE, vid)
                        if version is None:
                            raise LifecycleError(
                                f"引用的维度版本 id={vid} 不存在，元数据不能发布"
                            )
                        if version.get("status") != "已发布":
                            raise LifecycleError(
                                f"引用版本 {version['版本编号']} 结论为「{version['status']}」，"
                                "只有已发布版本可被元数据引用"
                            )
                        if not (version["生效日期"] <= on_date
                                <= _open_end(version.get("失效日期"))):
                            raise LifecycleError(
                                f"引用日期 {on_date} 不在版本 {version['版本编号']} 的生效区间内"
                            )
                        # 冻结引用快照：之后版本下线或出新版本，都不改写这条元数据的历史指向。
                        entry["引用版本id"] = vid
                        entry["引用版本编号"] = version["版本编号"]
                        entry["引用生效日期"] = on_date
                        entry["引用维度编号"] = version["维度编号"]
                        entry["引用自然键列"] = version["自然键列"]
                        entry["引用自然键值"] = version["自然键值"]
                        entry["引用代理键"] = version["代理键"]
                    elif not entry.get("引用版本id"):
                        entry.setdefault("引用版本编号", None)

                entry["status"] = target
                self.recompute_meta_entry(entry)
                after_map = self._capture([META_MODULE, DIM_MODULE, VERSION_MODULE])
                self._append_audit(
                    table="元数据项",
                    change_type=action,
                    before=before_entry,
                    after=copy.deepcopy(entry),
                    operator=operator,
                    reason=remark,
                )
                self._append_journal(
                    action=action,
                    version=None,
                    operator=operator,
                    reason=remark,
                    before_map=before_map,
                    after_map=after_map,
                    fallback_chain=f"metamodel:{entry_id}",
                    dim_no=str(entry.get("引用维度编号", "")),
                )
                return self.decorate_meta_entry(entry), f"元数据项已{action}"
        except LifecycleError as exc:
            return None, str(exc)

    # -------------------------------------------------------- 标志口径（唯一）
    def recompute_dim_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """维度表的 pending/abnormal 由版本台账结论推导，不再由本地动作自行决定。"""
        versions = [r for r in store.rows(VERSION_MODULE)
                    if r.get("维度编号") == entry.get("维度编号")]
        if not versions:
            entry["pending"] = entry.get("status") != "已下线"
            entry["abnormal"] = entry.get("status") == "已下线"
            return entry
        entry["pending"] = (entry.get("status") != "已下线"
                            and any(r.get("status") == "草稿" for r in versions)) or (
            entry.get("status") in ("草稿", "建表审批中"))
        open_versions = [r for r in versions
                         if r.get("status") == "已发布" and not r.get("失效日期")]
        # abnormal 跟随当前表结论：表已下线、或没有任何生效中版本（只剩历史/下线版本）才算异常；
        # 历史上存在过已下线版本不影响当前生效表的健康度。
        entry["abnormal"] = entry.get("status") == "已下线" or not open_versions
        if open_versions:
            current = max(open_versions, key=lambda r: r["生效日期"])
            entry["生效日期"] = current["生效日期"]
            entry["失效日期"] = None
        return entry

    def reference_state(self, entry: dict[str, Any]) -> tuple[str, bool] | None:
        version_id = entry.get("引用版本id")
        if version_id in (None, ""):
            return None
        version = store.find(VERSION_MODULE, int(version_id))
        if version is None:
            return "引用版本缺失", False
        status = version.get("status")
        if status == "草稿":
            return f"引用草稿版本 {version['版本编号']}", False
        if status == "已下线":
            return f"引用版本 {version['版本编号']} 已下线", False
        on_date = str(entry.get("引用生效日期") or version["生效日期"])
        inside = version["生效日期"] <= on_date <= _open_end(version.get("失效日期"))
        if not inside:
            return f"引用日期 {on_date} 不在版本区间", False
        if version.get("失效日期"):
            return f"历史引用 {version['版本编号']}（截至 {version['失效日期']}）", True
        return f"当前引用 {version['版本编号']}", True

    def recompute_meta_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """元数据项的 pending/abnormal 同样由版本结论推导。

        草稿/评审中允许先挂着未就绪的引用，不判异常；只有已经发布的元数据项引用了失效版本
        或日期越界（即「失效代理键仍被当作当前属性」）才置 abnormal，下线项同理。
        """
        entry["pending"] = entry.get("status") != "已下线"
        entry["abnormal"] = entry.get("status") == "已下线"
        if entry.get("status") == "已发布":
            state = self.reference_state(entry)
            if state is not None and not state[1]:
                entry["abnormal"] = True
        return entry

    def decorate_dim_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        versions = [r for r in store.rows(VERSION_MODULE)
                    if r.get("维度编号") == entry.get("维度编号")]
        open_versions = [r for r in versions
                         if r.get("status") == "已发布" and not r.get("失效日期")]
        decorated = dict(entry)
        decorated["版本总数"] = len(versions)
        decorated["待发布草稿数"] = sum(1 for r in versions if r.get("status") == "草稿")
        decorated["失效版本数"] = sum(1 for r in versions if r.get("status") == "已下线")
        if open_versions:
            current = max(open_versions, key=lambda r: r["生效日期"])
            decorated["当前版本编号"] = current["版本编号"]
            decorated["当前代理键"] = current["代理键"]
        else:
            decorated["当前版本编号"] = None
            decorated["当前代理键"] = None
        return decorated

    def decorate_meta_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(entry)
        state = self.reference_state(entry)
        decorated["引用状态"] = state[0] if state else "未绑定维度版本"
        decorated["引用有效"] = state[1] if state else False
        return decorated

    # ---------------------------------------------------------------- 内部
    def _decorate_version(self, row: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(row)
        chain_journals = [r for r in store.rows(UNDO_MODULE)
                          if r.get("链路键") == self._chain_key(row)]
        latest = max(chain_journals, key=lambda r: int(r.get("id", 0)), default=None)
        rollbackable = bool(latest is not None and not latest.get("已回滚")
                            and int(latest.get("关联版本id") or 0) == int(row["id"]))
        decorated["可回滚"] = rollbackable
        # 回滚接口需要的是回滚台账 id（与版本 id 不同源），一并给前端避免传错。
        decorated["可回滚台账id"] = int(latest["id"]) if rollbackable else None
        decorated["引用元数据数"] = sum(
            1 for r in store.rows(META_MODULE)
            if int(r.get("引用版本id") or 0) == int(row["id"]))
        decorated["失效日期展示"] = row.get("失效日期") or "至今"
        return decorated

    def _assert_no_interval_conflict(
        self,
        *,
        dimension_no: str,
        nk_column: str,
        nk_value: str,
        effective: str,
        expiry: str | None,
        ignore_id: int | None,
    ) -> None:
        for row in store.rows(VERSION_MODULE):
            if ignore_id is not None and int(row.get("id", 0)) == ignore_id:
                continue
            if (row.get("维度编号") != dimension_no
                    or row.get("自然键列") != nk_column
                    or row.get("自然键值") != nk_value):
                continue
            if not _overlaps(effective, expiry, row["生效日期"], row.get("失效日期")):
                continue
            if row.get("status") == "草稿":
                raise LifecycleError(
                    f"同一自然键已存在草稿 {row['版本编号']}（生效 {row['生效日期']}），"
                    "请先发布或处理该草稿，不能重复登记重叠版本"
                )
            if row.get("失效日期"):
                # 已闭合的已发布/已下线版本都是既定历史：已发布版本优先，草稿不得覆盖历史。
                raise LifecycleError(
                    f"生效区间与{row['status']}版本 {row['版本编号']} "
                    f"（{row['生效日期']} ~ {row['失效日期']}）冲突，"
                    "已发布版本优先，草稿不得覆盖历史区间"
                )
            # 开放中的已发布版本：只允许作为后继版本（新生效日严格晚于当前版本），
            # 发布时由协调器把旧版本 SCD2 闭合；反向插叙会改写历史，必须拒绝。
            if effective <= row["生效日期"]:
                raise LifecycleError(
                    f"新生效日期 {effective} 不晚于当前生效版本 {row['版本编号']} "
                    f"（{row['生效日期']} 起），不能反向覆盖已发布版本"
                )

    def _find_dim_entry(self, dimension_no: str) -> dict[str, Any] | None:
        for row in store.rows(DIM_MODULE):
            if str(row.get("维度编号", "")) == dimension_no:
                return row
        return None

    def _chain_key(self, version: dict[str, Any]) -> str:
        return "|".join([
            str(version.get("维度编号", "")),
            str(version.get("自然键列", "")),
            str(version.get("自然键值", "")),
        ])

    def _capture(self, modules: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {module: copy.deepcopy(store.rows(module)) for module in modules}

    def _before_version(
        self, before_map: dict[str, list[dict[str, Any]]], version_id: int
    ) -> dict[str, Any] | None:
        for row in before_map.get(VERSION_MODULE, []):
            if int(row.get("id", 0)) == version_id:
                return row
        return None

    def _append_audit(
        self,
        *,
        table: str,
        change_type: str,
        before: Any,
        after: Any,
        operator: str,
        reason: str,
    ) -> None:
        rows = store.rows(AUDIT_MODULE)
        new_id = store.next_id(AUDIT_MODULE)
        rows.append({
            "id": new_id,
            "status": "已记录",
            "pending": False,
            "abnormal": False,
            "记录编号": f"AUDI-{new_id:04d}",
            "操作表名": table,
            "变更类型": change_type,
            "变更前值": json.dumps(before, ensure_ascii=False, default=str),
            "变更后值": json.dumps(after, ensure_ascii=False, default=str),
            "操作人": operator or DEFAULT_OPERATOR,
            "操作时间": _today(),
            "变更原因": reason or "",
        })

    def _append_journal(
        self,
        *,
        action: str,
        version: dict[str, Any] | None,
        operator: str,
        reason: str,
        before_map: dict[str, list[dict[str, Any]]],
        after_map: dict[str, list[dict[str, Any]]],
        fallback_chain: str = "",
        dim_no: str = "",
    ) -> None:
        rows = store.rows(UNDO_MODULE)
        new_id = store.next_id(UNDO_MODULE)
        chain = self._chain_key(version) if version else (fallback_chain or dim_no or f"journal:{new_id}")
        rows.append({
            "id": new_id,
            "status": "已记录",
            "pending": False,
            "abnormal": False,
            "回滚编号": f"UV-{new_id:04d}",
            "维度编号": (version or {}).get("维度编号", dim_no),
            "链路键": chain,
            "关联动作": action,
            "关联版本id": int(version["id"]) if version else 0,
            "关联版本编号": (version or {}).get("版本编号", ""),
            "操作人": operator or DEFAULT_OPERATOR,
            "操作时间": _today(),
            "变更原因": reason or "",
            "已回滚": False,
            "快照": {"before": before_map, "after": after_map},
        })


coordinator = DimVersionService()
