import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/dim-version'

type VersionRow = Record<string, string | number | boolean | null>
type UndoRow = Record<string, string | number | boolean | null>
type RefRow = Record<string, string | number | boolean | null>

const versionColumns = [
  '版本编号', '维度编号', '自然键列', '自然键值', '代理键', '版本号',
  'status', '生效日期', '失效日期展示', '发布时间', '来源动作',
]
const undoColumns = ['回滚编号', '维度编号', '关联动作', '关联版本编号', '操作人', '操作时间', '已回滚']

export default function DimVersionView() {
  const [rows, setRows] = useState<VersionRow[]>([])
  const [total, setTotal] = useState(0)
  const [undoRows, setUndoRows] = useState<UndoRow[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState<Record<string, string>>(() => {
    // 支持从维度页/元数据页带维度编号跳转过来，跨页面联动直接定位。
    const initial: Record<string, string> = {}
    const dimensionNo = searchParams.get('dimension_no')
    if (dimensionNo) initial.dimension_no = dimensionNo
    return initial
  })
  const [resolveResult, setResolveResult] = useState<string>('')
  const [refsOf, setRefsOf] = useState<{ version: string; items: RefRow[] } | null>(null)
  const [draftForm, setDraftForm] = useState<Record<string, string>>({})

  const loadVersions = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('版本台账读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本台账读取失败')
    }
  }, [filters])

  const loadUndo = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/undo`)
      if (response.ok) {
        const payload = await response.json()
        setUndoRows(payload.items ?? [])
      }
    } catch {
      /* 回滚台账读取失败不阻塞主表 */
    }
  }, [])

  useEffect(() => {
    void loadVersions()
  }, [loadVersions])

  useEffect(() => {
    void loadUndo()
  }, [loadUndo])

  async function runAction(action: string, row: VersionRow, extra: Record<string, unknown> = {}) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action, ...extra } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || '版本动作未生效，两个业务面已保持原状态')
      }
      await Promise.all([loadVersions(), loadUndo()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本动作失败')
    }
  }

  async function createDraft() {
    setErrorMessage('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: draftForm }),
      })
      const payload = await response.json()
      if (payload.ok === false) throw new Error(payload.message)
      setDraftForm({})
      await Promise.all([loadVersions(), loadUndo()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '草稿登记失败')
    }
  }

  async function resolveByDate(form: Record<string, string>) {
    setErrorMessage('')
    const params = new URLSearchParams(form).toString()
    try {
      const response = await request(`${ENDPOINT}/resolve?${params}`)
      if (!response.ok) throw new Error('版本解析失败')
      const payload = await response.json()
      const items = payload.items as VersionRow[]
      setResolveResult(
        items.length
          ? `${form.on_date} 对应版本：${items.map((i) => `${i['版本编号']}（代理键 ${i['代理键']}，结论 ${i.status}）`).join('；')}`
          : `${form.on_date} 没有可解析的已发布版本（草稿不参与解析）`,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本解析失败')
    }
  }

  async function showRefs(row: VersionRow) {
    try {
      const response = await request(`${ENDPOINT}/${row.id}/refs`)
      if (!response.ok) throw new Error('引用关系读取失败')
      const payload = await response.json()
      setRefsOf({ version: String(row['版本编号']), items: payload.items ?? [] })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '引用关系读取失败')
    }
  }

  const filterFields = useMemo(() => ['dimension_no', 'natural_key', 'status'], [])
  const filterLabels: Record<string, string> = {
    dimension_no: '维度编号', natural_key: '自然键', status: '版本结论',
  }

  return (
    <section className="page" data-module="dim-version">
      <header className="page-head">
        <div>
          <h2>维度版本台账</h2>
          <p className="page-desc">
            维度表与元数据管理共用的唯一版本口径：维护自然键、代理键与生效/失效区间，发布、下线与历史保留
            都由同一条版本结论驱动；每次写入同步变更审计与回滚台账，两个业务面同成同退。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出版本台账
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">版本总数</span>
          <strong className="stat-value">{total}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">待发布草稿</span>
          <strong className="stat-value">{rows.filter((r) => r.status === '草稿').length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已下线保留版本</span>
          <strong className="stat-value">{rows.filter((r) => r.status === '已下线').length}</strong>
        </article>
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => { event.preventDefault(); void loadVersions() }}
      >
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{filterLabels[field]}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${filterLabels[field]}检索`}
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
            {versionColumns.map((column) => <th key={column}>{column}</th>)}
            <th>引用联动</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {versionColumns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void showRefs(row)}>
                  引用元数据({String(row['引用元数据数'] ?? 0)})
                </button>
              </td>
              <td className="row-actions">
                {row.status === '草稿' && (
                  <button className="link" type="button" onClick={() => void runAction('发布版本', row)}>
                    发布版本
                  </button>
                )}
                {row.status === '已发布' && !row['失效日期'] && (
                  <button className="link danger" type="button" onClick={() => void runAction('下线版本', row)}>
                    下线版本
                  </button>
                )}
                {row['可回滚'] ? (
                  <button
                    className="link"
                    type="button"
                    onClick={() => void runAction('回滚', row, { undo_id: row['可回滚台账id'] })}
                  >
                    回滚最近结论
                  </button>
                ) : (
                  <span className="muted-text">无可回滚结论</span>
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={versionColumns.length + 2}>暂无版本记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="panel-grid">
        <article className="side-panel">
          <h3>登记版本草稿</h3>
          <p className="page-desc">与已发布历史区间冲突会被拒绝：已发布版本优先，草稿不得覆盖历史。</p>
          {[
            ['维度编号', '如 DIM_CUSTOMER'],
            ['自然键列', '如 customer_no'],
            ['自然键值', '如 C1001'],
            ['代理键', '如 SK-1001-V3'],
            ['生效日期', 'YYYY-MM-DD'],
            ['失效日期', '留空表示至今'],
          ].map(([field, placeholder]) => (
            <label key={field} className="filter-item stack">
              <span>{field}</span>
              <input
                value={draftForm[field] ?? ''}
                placeholder={placeholder}
                onChange={(event) => setDraftForm((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            </label>
          ))}
          <button className="btn primary" type="button" onClick={() => void createDraft()}>登记草稿</button>
        </article>

        <article className="side-panel">
          <h3>按日期解析版本</h3>
          <p className="page-desc">元数据发布按该口径定位版本，已下线/已闭合的历史版本仍可追溯。</p>
          <ResolveForm onResolve={(form) => void resolveByDate(form)} />
          {resolveResult && <p className="resolve-result">{resolveResult}</p>}
        </article>
      </div>

      <h3>回滚台账（按链路倒序回滚，行级快照整体恢复并同步审计）</h3>
      <table className="data-table">
        <thead>
          <tr>{undoColumns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {undoRows.map((row) => (
            <tr key={String(row.id)}>
              {undoColumns.map((column) => (
                <td key={column}>{typeof row[column] === 'boolean' ? (row[column] ? '是' : '否') : String(row[column] ?? '—')}</td>
              ))}
            </tr>
          ))}
          {undoRows.length === 0 && (
            <tr><td className="empty-state" colSpan={undoColumns.length}>暂无回滚记录</td></tr>
          )}
        </tbody>
      </table>

      {refsOf && (
        <div className="modal-mask" role="dialog" aria-modal="true" onClick={() => setRefsOf(null)}>
          <div className="modal-card" onClick={(event) => event.stopPropagation()}>
            <header className="page-head">
              <h3>版本 {refsOf.version} 的元数据引用</h3>
              <button className="btn ghost" type="button" onClick={() => setRefsOf(null)}>关闭</button>
            </header>
            <table className="data-table">
              <thead>
                <tr><th>元数据编号</th><th>对象名称</th><th>引用代理键</th><th>引用状态</th></tr>
              </thead>
              <tbody>
                {refsOf.items.map((item) => (
                  <tr key={String(item.id)}>
                    <td>{String(item['元数据编号'] ?? '—')}</td>
                    <td>{String(item['对象名称'] ?? '—')}</td>
                    <td>{String(item['引用代理键'] ?? '—')}</td>
                    <td>{String(item['引用状态'] ?? '—')}</td>
                  </tr>
                ))}
                {refsOf.items.length === 0 && (
                  <tr><td className="empty-state" colSpan={4}>暂无元数据引用该版本</td></tr>
                )}
              </tbody>
            </table>
            <p className="page-desc">跳转到<a className="link" href="/metamodel">元数据管理页</a>可查看完整引用口径。</p>
          </div>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条版本记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}

function ResolveForm({ onResolve }: { onResolve: (form: Record<string, string>) => void }) {
  const [form, setForm] = useState<Record<string, string>>({
    dimension_no: 'DIM_CUSTOMER',
    natural_key_column: 'customer_no',
    natural_key_value: 'C1001',
    on_date: '2026-09-15',
  })
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        onResolve(form)
      }}
    >
      {[
        ['dimension_no', '维度编号'],
        ['natural_key_column', '自然键列'],
        ['natural_key_value', '自然键值'],
        ['on_date', '解析日期'],
      ].map(([field, label]) => (
        <label key={field} className="filter-item stack">
          <span>{label}</span>
          <input
            value={form[field] ?? ''}
            onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
          />
        </label>
      ))}
      <button className="btn" type="submit">解析版本</button>
    </form>
  )
}
