"""维度版本生命周期重构的回归测试。

覆盖：
* 版本台账按日期解析，草稿不参与解析，已下线/已闭合历史版本保留可追溯；
* 同自然键生效区间冲突时已发布版本优先，未发布草稿不得覆盖历史；
* 发布/下线/回滚由同一条版本结论驱动维度表与元数据两个业务面，同成同退；
* 元数据项冻结版本快照，版本下线或出新版本不会批量改写历史引用；
* 每次结论同步变更审计与回滚台账，回滚按链路倒序、行级快照逆向恢复。
"""
from __future__ import annotations

import copy
import unittest

from app.seed import SEED_ROWS
from app.services.dim import DimService
from app.services.dim_version import (
    DIM_MODULE,
    META_MODULE,
    UNDO_MODULE,
    VERSION_MODULE,
    LifecycleError,
    coordinator,
    transaction,
)
from app.services.metamodel import MetamodelService
from app.store import store


def reset_store() -> None:
    store.restore({name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()})


class DimVersionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.dim_service = DimService()
        self.meta_service = MetamodelService()

    # ------------------------------------------------------------ 解析口径
    def test_resolve_returns_version_for_each_date_and_ignores_drafts(self) -> None:
        old = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1001", "2026-09-15")
        current = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1001", "2026-10-05")
        future = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1001", "2026-12-01")
        self.assertEqual([v["版本编号"] for v in old], ["DV-0001"])
        self.assertEqual(old[0]["代理键"], "SK-1001-V1")
        self.assertEqual([v["版本编号"] for v in current], ["DV-0002"])
        # DV-0004 是草稿，任何日期都不能被解析出来。
        self.assertEqual([v["版本编号"] for v in future if v["版本编号"] == "DV-0004"], [])
        # 已下线版本仍可按历史日期追溯。
        offline = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1002", "2026-05-01")
        self.assertEqual([v["版本编号"] for v in offline], ["DV-0003"])
        self.assertEqual(offline[0]["status"], "已下线")

    def test_metadata_keeps_each_date_version_not_bulk_repointed(self) -> None:
        """场景题眼：新代理键上线后，旧元数据仍指向各自日期对应的版本。"""
        meta1 = store.find(META_MODULE, 1)
        meta2 = store.find(META_MODULE, 2)
        self.assertEqual(meta1["引用代理键"], "SK-1001-V1")
        self.assertEqual(meta2["引用代理键"], "SK-1001-V2")
        decorated1 = coordinator.decorate_meta_entry(meta1)
        self.assertTrue(decorated1["引用有效"])
        self.assertIn("历史引用", decorated1["引用状态"])

    # ------------------------------------------------------------ 冲突规则
    def test_draft_overlapping_published_history_is_rejected(self) -> None:
        audit_before = len(store.rows("auditlog"))
        snapshot = store.snapshot()
        entry, message = coordinator.create_draft({
            "维度编号": "DIM_CUSTOMER", "自然键列": "customer_no", "自然键值": "C1001",
            "代理键": "SK-BAD", "生效日期": "2026-05-01",
        })
        self.assertIsNone(entry)
        self.assertIn("已发布版本优先", message)
        # 被拒绝的写入必须整体退回：审计不新增、整库与快照一致。
        self.assertEqual(len(store.rows("auditlog")), audit_before)
        self.assertEqual(store.snapshot().keys(), snapshot.keys())
        self.assertEqual(
            [r["代理键"] for r in store.rows(VERSION_MODULE)],
            [r["代理键"] for r in snapshot[VERSION_MODULE]],
        )

    def test_draft_overlapping_another_draft_is_rejected(self) -> None:
        entry, message = coordinator.create_draft({
            "维度编号": "DIM_CUSTOMER", "自然键列": "customer_no", "自然键值": "C1001",
            "代理键": "SK-DUP", "生效日期": "2026-11-20",
        })
        self.assertIsNone(entry)
        self.assertIn("DV-0004", message)

    def test_publishing_draft_conflicting_at_publish_time_rolls_back(self) -> None:
        # 先造一条与任何区间都不重叠的草稿（不同自然键值、更晚日期，登记时合法），
        # 再把自然键值改成 C1001、生效日提前到开放版本区间内，模拟发布时刻才暴露的冲突。
        entry, _ = coordinator.create_draft({
            "维度编号": "DIM_CUSTOMER", "自然键列": "customer_no", "自然键值": "C9999",
            "代理键": "SK-X", "生效日期": "2026-12-20",
        })
        self.assertIsNotNone(entry)
        draft = store.find(VERSION_MODULE, entry["id"])
        draft["自然键值"] = "C1001"
        draft["生效日期"] = "2026-09-15"   # 落进已闭合历史版本 DV-0001 的区间
        draft["失效日期"] = "2026-09-20"   # 截止日早于草稿 DV-0004，避免先撞草稿
        audit_before = len(store.rows("auditlog"))
        result, message = coordinator.publish_version(entry["id"])
        self.assertIsNone(result)
        self.assertIn("冲突", message)
        self.assertIn("已发布版本优先", message)
        self.assertEqual(draft["status"], "草稿")  # 事务回滚，结论未被改写
        self.assertEqual(len(store.rows("auditlog")), audit_before)

    # ------------------------------------------------------------ 发布结论
    def test_publish_closes_older_version_without_touching_metadata_snapshot(self) -> None:
        version, message = coordinator.publish_version(4)  # DV-0004 @2026-11-15
        self.assertIsNotNone(version, message)
        older = store.find(VERSION_MODULE, 2)
        self.assertEqual(older["失效日期"], "2026-11-15")  # SCD2 闭合
        self.assertEqual(older["代理键"], "SK-1001-V2")    # 历史代理键不改写
        self.assertEqual(store.find(VERSION_MODULE, 4)["status"], "已发布")
        # 元数据历史引用原样保留，没有被批量指向最新版本。
        self.assertEqual(store.find(META_MODULE, 1)["引用代理键"], "SK-1001-V1")
        self.assertEqual(store.find(META_MODULE, 2)["引用代理键"], "SK-1001-V2")
        # 维度表结论与当前版本对齐。
        dim = coordinator.decorate_dim_entry(store.find(DIM_MODULE, 1))
        self.assertEqual(dim["当前版本编号"], "DV-0004")
        self.assertEqual(dim["生效日期"], "2026-11-15")
        self.assertFalse(dim["abnormal"])

    def test_publish_appends_audit_and_undo_journal(self) -> None:
        audit_before = len(store.rows("auditlog"))
        coordinator.publish_version(4)
        audits = store.rows("auditlog")[audit_before:]
        self.assertTrue(any(a["变更类型"] == "发布版本" for a in audits))
        journals = store.rows(UNDO_MODULE)
        self.assertEqual(len(journals), 1)
        self.assertIn("before", journals[0]["快照"])
        self.assertFalse(journals[0]["已回滚"])

    def test_only_draft_can_be_published_and_duplicate_submit_is_idempotent(self) -> None:
        result, message = coordinator.publish_version(2)  # DV-0002 已发布
        self.assertIsNone(result)
        self.assertIn("只有草稿才能发布", message)
        # 维度表重复提交同一动作：幂等返回，不新增台账/审计。
        # DIM_PRODUCT（id=2）当前就是「建表审批中」，重复提交命中幂等分支。
        journal_before = len(store.rows(UNDO_MODULE))
        audit_before = len(store.rows("auditlog"))
        entry, _ = self.dim_service.run_action(2, "提交建表审批")
        self.assertIsNotNone(entry)
        entry2, msg2 = self.dim_service.run_action(2, "提交建表审批")
        self.assertIsNotNone(entry2)
        self.assertIn("无需重复提交", msg2)
        self.assertEqual(len(store.rows(UNDO_MODULE)), journal_before)
        self.assertEqual(len(store.rows("auditlog")), audit_before)

    # ------------------------------------------------------------ 下线结论
    def test_offline_current_version_marks_both_faces_and_keeps_history(self) -> None:
        result, message = coordinator.offline_version(2, offline_date="2026-11-10")
        self.assertIsNotNone(result, message)
        version = store.find(VERSION_MODULE, 2)
        self.assertEqual(version["status"], "已下线")
        self.assertEqual(version["失效日期"], "2026-11-10")
        self.assertTrue(version["abnormal"])
        # 历史版本不被改写成未发生。
        history = store.find(VERSION_MODULE, 1)
        self.assertEqual(history["失效日期"], "2026-09-30")
        self.assertEqual(history["代理键"], "SK-1001-V1")
        # 维度表跟随同一条结论进入下线/异常。
        dim = store.find(DIM_MODULE, 1)
        self.assertEqual(dim["status"], "已下线")
        self.assertTrue(dim["abnormal"])
        # 引用当前版本的元数据转异常；引用历史版本的元数据仍有效。
        meta2 = coordinator.decorate_meta_entry(store.find(META_MODULE, 2))
        self.assertTrue(meta2["abnormal"])
        self.assertFalse(coordinator.decorate_meta_entry(store.find(META_MODULE, 1))["abnormal"])

    def test_closed_history_version_cannot_be_offlined_again(self) -> None:
        result, message = coordinator.offline_version(1)  # DV-0001 已闭合
        self.assertIsNone(result)
        self.assertIn("历史保留不可改写", message)
        result, message = coordinator.offline_version(3)  # DV-0003 已下线
        self.assertIsNone(result)
        self.assertIn("不能重复下线", message)

    # ------------------------------------------------------------ 元数据发布
    def test_metadata_publish_rejects_draft_and_offline_references(self) -> None:
        # META-0003 引用草稿 DV-0004，直接发布必须被拦。
        result, message = self.meta_service.run_action(3, "发布元数据", values={"引用版本id": 4})
        self.assertIsNone(result)
        self.assertIn("只有已发布版本可被元数据引用", message)
        # 已下线版本同样不能作为发布引用。
        result, message = self.meta_service.run_action(
            3, "发布元数据", values={"引用版本id": 3, "引用生效日期": "2026-05-01"}
        )
        self.assertIsNone(result)
        self.assertIn("已下线", message)

    def test_metadata_publish_freezes_version_snapshot(self) -> None:
        result, message = self.meta_service.run_action(
            3, "发布元数据",
            values={"引用版本id": 2, "引用生效日期": "2026-10-05"},
        )
        self.assertIsNotNone(result, message)
        meta = store.find(META_MODULE, 3)
        self.assertEqual(meta["引用代理键"], "SK-1001-V2")
        self.assertEqual(meta["引用版本编号"], "DV-0002")
        # 随后下线 DV-0002，冻结快照不被改写，只是引用状态转异常。
        coordinator.offline_version(2, offline_date="2026-11-10")
        meta_after = store.find(META_MODULE, 3)
        self.assertEqual(meta_after["引用代理键"], "SK-1001-V2")
        self.assertTrue(coordinator.decorate_meta_entry(meta_after)["abnormal"])

    def test_metadata_publish_rejects_date_outside_interval(self) -> None:
        result, message = self.meta_service.run_action(
            3, "发布元数据",
            values={"引用版本id": 1, "引用生效日期": "2026-10-05"},  # DV-0001 止于 09-30
        )
        self.assertIsNone(result)
        self.assertIn("不在版本", message)

    def test_failed_metadata_write_rolls_back_both_faces(self) -> None:
        before = store.snapshot()
        result, _ = self.meta_service.run_action(3, "发布元数据", values={"引用版本id": 4})
        self.assertIsNone(result)
        # 整库回滚：元数据项状态、维度表、审计全部保持原样。
        self.assertEqual(store.find(META_MODULE, 3)["status"], "草稿")
        self.assertEqual(
            store.find(DIM_MODULE, 1)["status"], before[DIM_MODULE][0]["status"]
        )
        self.assertEqual(len(store.rows("auditlog")), len(before["auditlog"]))

    # ------------------------------------------------------------ 回滚
    def test_rollback_restores_row_snapshots_and_appends_audit(self) -> None:
        coordinator.publish_version(4)
        journal = store.rows(UNDO_MODULE)[-1]
        audit_before = len(store.rows("auditlog"))
        result, message = coordinator.rollback(journal["id"])
        self.assertIsNotNone(result, message)
        # 版本结论逆向恢复：新草稿回退、旧版本重新开放。
        self.assertEqual(store.find(VERSION_MODULE, 4)["status"], "草稿")
        self.assertIsNone(store.find(VERSION_MODULE, 2)["失效日期"])
        self.assertTrue(store.find(UNDO_MODULE, journal["id"])["已回滚"])
        self.assertEqual(len(store.rows("auditlog")), audit_before + 1)
        self.assertEqual(store.rows("auditlog")[-1]["变更类型"], "回滚")

    def test_rollback_must_follow_chain_order_and_no_double_rollback(self) -> None:
        coordinator.publish_version(4)
        first = store.rows(UNDO_MODULE)[-1]
        coordinator.offline_version(4, offline_date="2026-11-20")
        # 链路里先发布后下线，必须先回滚下线，不能跳着回滚发布。
        result, message = coordinator.rollback(first["id"])
        self.assertIsNone(result)
        self.assertIn("倒序", message)
        latest = store.rows(UNDO_MODULE)[-1]
        ok, _ = coordinator.rollback(latest["id"])
        self.assertIsNotNone(ok)
        result, message = coordinator.rollback(latest["id"])
        self.assertIsNone(result)
        self.assertIn("已执行过", message)

    def test_dim_table_offline_drives_version_conclusions_in_one_transaction(self) -> None:
        entry, message = self.dim_service.run_action(1, "下线表")
        self.assertIsNotNone(entry, message)
        # DV-0002 是开放版本，被同一结论联动下线；历史 DV-0001 不动。
        self.assertEqual(store.find(VERSION_MODULE, 2)["status"], "已下线")
        self.assertEqual(store.find(VERSION_MODULE, 1)["status"], "已发布")
        self.assertEqual(store.find(DIM_MODULE, 1)["status"], "已下线")
        # 审计与回滚台账都有同步记录。
        self.assertTrue(any(a["变更类型"] == "下线表" for a in store.rows("auditlog")))
        self.assertTrue(any(j["关联动作"] == "下线表" for j in store.rows(UNDO_MODULE)))

    def test_dim_online_requires_published_version(self) -> None:
        # DIM_REGION（id=3）台账里没有任何版本，上线必须被拦。
        result, message = self.dim_service.run_action(3, "确认上线")
        self.assertIsNone(result)
        self.assertIn("先在维度版本台账发布版本", message)

    def test_transaction_context_restores_on_arbitrary_error(self) -> None:
        before = store.snapshot()
        with self.assertRaises(RuntimeError):
            with transaction():
                coordinator.publish_version(4)
                raise RuntimeError("模拟下游故障")
        self.assertEqual(store.find(VERSION_MODULE, 4)["status"], "草稿")
        self.assertIsNone(store.find(VERSION_MODULE, 2)["失效日期"])
        self.assertEqual(
            len(store.rows("auditlog")), len(before["auditlog"])
        )

    def test_unknown_action_is_rejected_without_side_effects(self) -> None:
        before = len(store.rows("auditlog"))
        result, message = self.dim_service.run_action(1, "删除表")
        self.assertIsNone(result)
        self.assertIn("不属于维度层可执行范围", message)
        self.assertEqual(len(store.rows("auditlog")), before)

    def test_internal_ledger_hidden_from_overview(self) -> None:
        names = {m["name"] for m in store.overview()["modules"]}
        self.assertNotIn("dim_version", names)
        self.assertNotIn("dim_version_undo", names)


class DimCustomerAcceptanceTests(unittest.TestCase):
    """题目场景验收：DIM_CUSTOMER / customer_no 在 2026-10-01 产生新代理键。"""

    def setUp(self) -> None:
        reset_store()
        self.dim_service = DimService()
        self.meta_service = MetamodelService()

    def test_full_narrative(self) -> None:
        # 1) 旧版本失效后，两条元数据仍各自指向对应日期的版本，而非批量指向最新。
        sep = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1001", "2026-09-15")
        octo = coordinator.resolve("DIM_CUSTOMER", "customer_no", "C1001", "2026-10-01")
        self.assertEqual(sep[0]["代理键"], "SK-1001-V1")
        self.assertEqual(octo[0]["代理键"], "SK-1001-V2")
        self.assertEqual(store.find(META_MODULE, 1)["引用代理键"], "SK-1001-V1")
        self.assertEqual(store.find(META_MODULE, 2)["引用代理键"], "SK-1001-V2")

        # 2) 同自然键生效区间冲突：已发布版本优先，草稿不能覆盖历史。
        bad, message = coordinator.create_draft({
            "维度编号": "DIM_CUSTOMER", "自然键列": "customer_no", "自然键值": "C1001",
            "代理键": "SK-BAD", "生效日期": "2026-09-10",
        })
        self.assertIsNone(bad)
        self.assertIn("已发布版本优先", message)

        # 3) 当前版本下线：两个业务面同结论，历史版本保留且历史元数据不受影响。
        ok, _ = coordinator.offline_version(2, offline_date="2026-11-10")
        self.assertIsNotNone(ok)
        self.assertEqual(store.find(DIM_MODULE, 1)["status"], "已下线")
        self.assertTrue(store.find(DIM_MODULE, 1)["abnormal"])
        self.assertTrue(self.meta_service.get_entry(2)["abnormal"])
        self.assertFalse(self.meta_service.get_entry(1)["abnormal"])
        # 已失效版本仍可追溯，没有被改写成“未发生”。
        self.assertEqual(store.find(VERSION_MODULE, 1)["代理键"], "SK-1001-V1")
        self.assertEqual(store.find(VERSION_MODULE, 1)["失效日期"], "2026-09-30")

        # 4) 回滚同步两个业务面与审计，且只允许回滚最近一次链路结论。
        journal = store.rows(UNDO_MODULE)[-1]
        ok, _ = coordinator.rollback(journal["id"])
        self.assertIsNotNone(ok)
        self.assertEqual(store.find(VERSION_MODULE, 2)["status"], "已发布")
        self.assertIsNone(store.find(VERSION_MODULE, 2)["失效日期"])
        self.assertEqual(store.find(DIM_MODULE, 1)["status"], "已上线")
        self.assertFalse(self.meta_service.get_entry(2)["abnormal"])
        self.assertTrue(any(a["变更类型"] == "回滚" for a in store.rows("auditlog")))

    def test_create_metadata_with_reference_fields_is_preserved(self) -> None:
        entry, missing = self.meta_service.create_entry({
            "元数据编号": "META-0009", "对象类型": "维度属性", "对象名称": "客户标签",
            "引用版本id": 2, "引用版本编号": "DV-0002", "引用代理键": "SK-1001-V2",
            "引用生效日期": "2026-10-05",
        })
        self.assertEqual(missing, [])
        self.assertEqual(entry["引用代理键"], "SK-1001-V2")
        self.assertEqual(entry["引用状态"], "当前引用 DV-0002")


if __name__ == "__main__":
    unittest.main()
