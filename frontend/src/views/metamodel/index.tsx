import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/metamodel'
const columns = [
  "元数据编号",
  "对象类型",
  "对象名称",
  "属性名称",
  "属性值",
  "引用版本编号",
  "引用代理键",
  "引用生效日期",
  "引用状态"
]
const actions = [
  "提交元数据评审",
  "发布元数据",
  "下线元数据"
]
const statuses = [
  "草稿",
  "元数据评审中",
  "已发布",
  "已下线"
]

type Row = Record<string, string | number | boolean | null>
type VersionOption = Record<string, string | number | null>

export default function MetamodelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [versionOptions, setVersionOptions] = useState<VersionOption[]>([])
  const [publishing, setPublishing] = useState<Row | null>(null)
  const [publishForm, setPublishForm] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ["元数据编号", "对象类型", "对象名称"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('元数据项列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据管理列表读取失败')
    }
  }, [filters])

  const loadPublishedVersions = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT.replace('/metamodel', '/dim-version')}?status=${encodeURIComponent('已发布')}&size=200`)
      if (response.ok) {
        const payload = await response.json()
        setVersionOptions(payload.items ?? [])
      }
    } catch {
      /* 版本选项加载失败时仍可手动录入引用字段 */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void loadPublishedVersions()
  }, [loadPublishedVersions])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || '元数据管理动作未生效，两个业务面已保持原状态')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据管理操作失败')
    }
  }

  function openPublishDialog(row: Row) {
    setPublishing(row)
    setPublishForm({
      引用版本id: String(row['引用版本id'] ?? ''),
      引用生效日期: String(row['引用生效日期'] ?? ''),
    })
  }

  async function confirmPublish() {
    if (!publishing) return
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${publishing.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action: '发布元数据', ...publishForm } }),
      })
      const payload = await response.json()
      if (payload.ok === false) throw new Error(payload.message)
      setPublishing(null)
      await Promise.all([reload(), loadPublishedVersions()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据发布失败')
    }
  }

  return (
    <section className="page" data-module="metamodel">
      <header className="page-head">
        <div>
          <h2>元数据管理</h2>
          <p className="page-desc">
            元数据项只引用明确的维度版本：发布时校验版本结论与生效日期并冻结代理键快照，
            版本下线或出新版本都不会批量改写历史引用。版本口径见
            <a className="link inline-link" href="/dim-version">维度版本台账</a>。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('元数据项登记入口尚未接入审批流')}>
            登记元数据项
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出元数据管理清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">元数据项总数</span>
          <strong className="stat-value">{total}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已发布项</span>
          <strong className="stat-value">{rows.filter((r) => r.status === '已发布').length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">引用异常项</span>
          <strong className="stat-value">{rows.filter((r) => r.abnormal === true).length}</strong>
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
            <th>版本联动</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row.abnormal === true ? 'row-abnormal' : ''}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
              <td className="row-actions">
                {row['引用版本id'] ? (
                  <a
                    className="link"
                    href={`/dim-version?dimension_no=${encodeURIComponent(String(row['引用维度编号'] ?? ''))}`}
                  >
                    查看引用版本
                  </a>
                ) : (
                  <span className="muted-text">未绑定</span>
                )}
              </td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button
                    key={action}
                    className={`link${action === '下线元数据' ? ' danger' : ''}`}
                    type="button"
                    onClick={() => action === '发布元数据' ? openPublishDialog(row) : void runAction(action, row)}
                  >
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无元数据管理数据，可先登记元数据项</td>
            </tr>
          )}
        </tbody>
      </table>

      {publishing && (
        <div className="modal-mask" role="dialog" aria-modal="true" onClick={() => setPublishing(null)}>
          <div className="modal-card" onClick={(event) => event.stopPropagation()}>
            <header className="page-head">
              <h3>发布元数据 {String(publishing['元数据编号'] ?? '')}</h3>
              <button className="btn ghost" type="button" onClick={() => setPublishing(null)}>取消</button>
            </header>
            <p className="page-desc">
              只能选择结论为「已发布」且覆盖引用日期的维度版本；确认后冻结代理键快照，历史引用不再被改写。
            </p>
            <label className="filter-item stack">
              <span>引用维度版本</span>
              <select
                value={publishForm['引用版本id'] ?? ''}
                onChange={(event) => {
                  const picked = versionOptions.find((v) => String(v.id) === event.target.value)
                  setPublishForm((prev) => ({
                    ...prev,
                    引用版本id: event.target.value,
                    引用生效日期: picked ? String(picked['生效日期'] ?? '') : prev['引用生效日期'],
                  }))
                }}
              >
                <option value="">请选择已发布版本</option>
                {versionOptions.map((version) => (
                  <option key={String(version.id)} value={String(version.id)}>
                    {String(version['版本编号'])} / {String(version['维度编号'])} /
                    代理键 {String(version['代理键'])} / {String(version['生效日期'])} 起
                  </option>
                ))}
              </select>
            </label>
            <label className="filter-item stack">
              <span>引用生效日期</span>
              <input
                value={publishForm['引用生效日期'] ?? ''}
                placeholder="YYYY-MM-DD"
                onChange={(event) => setPublishForm((prev) => ({ ...prev, 引用生效日期: event.target.value }))}
              />
            </label>
            <button className="btn primary" type="button" onClick={() => void confirmPublish()}>确认发布</button>
          </div>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条元数据记录（status 取值：{statuses.join('、')}）</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
