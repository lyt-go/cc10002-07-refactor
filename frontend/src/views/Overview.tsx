import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const fallbackCards = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
const fallbackModules = [{"name": "原始落地", "created": 0, "pending": 0, "abnormal": 0}, {"name": "贴源层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "明细层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "维度层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "汇总层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "应用层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分区治理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "质量校验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据血缘", "created": 0, "pending": 0, "abnormal": 0}, {"name": "历史补数", "created": 0, "pending": 0, "abnormal": 0}, {"name": "清洗规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "转换编排", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据字典", "created": 0, "pending": 0, "abnormal": 0}, {"name": "元数据管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据生命周期", "created": 0, "pending": 0, "abnormal": 0}, {"name": "存储成本", "created": 0, "pending": 0, "abnormal": 0}, {"name": "同步通道", "created": 0, "pending": 0, "abnormal": 0}, {"name": "变更审计", "created": 0, "pending": 0, "abnormal": 0}]

export default function Overview() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
      })
      .catch(() => undefined)
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
