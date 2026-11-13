import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dim'
const columns = [
  "维度编号",
  "维度名称",
  "自然键列",
  "代理键列",
  "历史策略",
  "生效日期",
  "失效日期",
  "当前版本编号",
  "当前代理键",
  "版本总数",
  "待发布草稿数",
  "失效版本数"
]
const actions = [
  "提交建表审批",
  "确认上线",
  "下线表"
]
const statuses = [
  "草稿",
  "建表审批中",
  "已上线",
  "已下线"
]

type Row = Record<string, string | number | boolean | null>

export default function DimView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ["维度编号", "维度名称", "自然键列"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('维度表列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '维度层列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || '维度层动作未生效，两个业务面已保持原状态')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '维度层操作失败')
    }
  }

  return (
    <section className="page" data-module="dim">
      <header className="page-head">
        <div>
          <h2>维度层管理</h2>
          <p className="page-desc">
            维度表只维护自然键、代理键列与生效/失效区间；版本结论统一由
            <a className="link inline-link" href="/dim-version">维度版本台账</a>
            驱动，发布、下线与回滚会同口径同步元数据管理与变更审计。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('维度表登记入口尚未接入审批流')}>
            登记维度表
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出维度层清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">维度表总数</span>
          <strong className="stat-value">{total}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已上线表</span>
          <strong className="stat-value">{rows.filter((r) => r.status === '已上线').length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">含待发布草稿的表</span>
          <strong className="stat-value">{rows.filter((r) => Number(r['待发布草稿数']) > 0).length}</strong>
        </article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>版本台账</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
              <td className="row-actions">
                <a
                  className="link"
                  href={`/dim-version?dimension_no=${encodeURIComponent(String(row['维度编号'] ?? ''))}`}
                >
                  查看版本台账
                </a>
              </td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无维度层数据，可先登记维度表</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条维度层记录（status 取值：{statuses.join('、')}）</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
