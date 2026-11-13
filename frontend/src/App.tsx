import { useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'

import Overview from './views/Overview'
import LandingView from './views/landing'
import OdsView from './views/ods'
import DwdView from './views/dwd'
import DimView from './views/dim'
import DimVersionView from './views/dimversion'
import DwsView from './views/dws'
import AdsView from './views/ads'
import PartitionView from './views/partition'
import QualityView from './views/quality'
import LineageView from './views/lineage'
import BackfillView from './views/backfill'
import CleanruleView from './views/cleanrule'
import TransformView from './views/transform'
import DictionaryView from './views/dictionary'
import MetamodelView from './views/metamodel'
import LifecycleView from './views/lifecycle'
import CostView from './views/cost'
import SyncView from './views/sync'
import AuditlogView from './views/auditlog'

const navItems = [{ label: "运营概览", path: "/" }, { label: "原始落地", path: "/landing" }, { label: "贴源层", path: "/ods" }, { label: "明细层", path: "/dwd" }, { label: "维度层", path: "/dim" }, { label: "维度版本台账", path: "/dim-version" }, { label: "汇总层", path: "/dws" }, { label: "应用层", path: "/ads" }, { label: "分区治理", path: "/partition" }, { label: "质量校验", path: "/quality" }, { label: "数据血缘", path: "/lineage" }, { label: "历史补数", path: "/backfill" }, { label: "清洗规则", path: "/cleanrule" }, { label: "转换编排", path: "/transform" }, { label: "数据字典", path: "/dictionary" }, { label: "元数据管理", path: "/metamodel" }, { label: "数据生命周期", path: "/lifecycle" }, { label: "存储成本", path: "/cost" }, { label: "同步通道", path: "/sync" }, { label: "变更审计", path: "/auditlog" }]

export default function App() {
  const [operator] = useState('值班管理员')

  return (
    <div className="app-shell">
      <aside className="app-side">
        <h1 className="app-title">数仓分层建模与治理平台</h1>
        <nav className="nav-list">
          {navItems.map((item) => (
            <Link key={item.path} to={item.path} className="nav-item">
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="app-main">
        <header className="app-head">
          <span className="head-desc">面向原始数据落地、清洗转换、分层建模、维度事实设计、分区治理、质量校验、血缘追踪与历史补数的数据仓库工程管理平台。</span>
          <span className="head-user">当前值班：{operator}</span>
        </header>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/landing" element={<LandingView />} />
          <Route path="/ods" element={<OdsView />} />
          <Route path="/dwd" element={<DwdView />} />
          <Route path="/dim" element={<DimView />} />
          <Route path="/dim-version" element={<DimVersionView />} />
          <Route path="/dws" element={<DwsView />} />
          <Route path="/ads" element={<AdsView />} />
          <Route path="/partition" element={<PartitionView />} />
          <Route path="/quality" element={<QualityView />} />
          <Route path="/lineage" element={<LineageView />} />
          <Route path="/backfill" element={<BackfillView />} />
          <Route path="/cleanrule" element={<CleanruleView />} />
          <Route path="/transform" element={<TransformView />} />
          <Route path="/dictionary" element={<DictionaryView />} />
          <Route path="/metamodel" element={<MetamodelView />} />
          <Route path="/lifecycle" element={<LifecycleView />} />
          <Route path="/cost" element={<CostView />} />
          <Route path="/sync" element={<SyncView />} />
          <Route path="/auditlog" element={<AuditlogView />} />
        </Routes>
      </main>
    </div>
  )
}
