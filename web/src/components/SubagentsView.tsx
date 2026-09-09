import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { SubagentInfo, SubagentRegistry } from '../types'

const EMPTY: SubagentInfo = {
  id: '',
  name: '',
  emoji: '🤖',
  description: '',
  persona: '',
  providerType: '',
  model: '',
  tools: [],
  skills: [],
  mcpServers: [],
  maxRounds: 6,
}

/**
 * SubagentsView - 助理（子代理）管理
 *
 * 一个 subagent = 一份「人设 + 自己的模型 + 自己的工具/技能/MCP」配置，
 * 主 agent 通过 subagent_delegate 工具把任务派给它跑独立循环。
 *
 * 页面做三件事：
 *  1. 列出已有 subagent；
 *  2. 手动新建/编辑（只能从 /api/subagents/registry 返回的真实资源里选）；
 *  3. 「让 AI 规划」——把注册表交给主 agent 的 LLM，由它设计一份候选配置，
 *     用户确认后再保存。
 */
export function SubagentsView() {
  const [items, setItems] = useState<SubagentInfo[]>([])
  const [registry, setRegistry] = useState<SubagentRegistry | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [draft, setDraft] = useState<SubagentInfo | null>(null)
  const [isEdit, setIsEdit] = useState(false)
  const [busy, setBusy] = useState(false)

  const [planText, setPlanText] = useState('')
  const [planning, setPlanning] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.subagentsList()
      setItems(data.subagents)
      setRegistry(data.registry)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const usableProviders = useMemo(
    () => (registry?.providers ?? []).filter((p) => p.usable),
    [registry],
  )

  const modelsFor = useCallback(
    (ptype: string) =>
      (registry?.models ?? []).filter((m) => m.providerType === ptype),
    [registry],
  )

  const startNew = () => {
    const p = usableProviders[0]
    setDraft({
      ...EMPTY,
      providerType: p?.type ?? '',
      model: p?.model ?? '',
      tools: p ? ['file_read', 'file_write'] : [],
    })
    setIsEdit(false)
    setNotice('')
    setError('')
  }

  const startEdit = (s: SubagentInfo) => {
    setDraft({ ...EMPTY, ...s })
    setIsEdit(true)
    setNotice('')
    setError('')
  }

  const save = async () => {
    if (!draft) return
    setBusy(true)
    setNotice('')
    setError('')
    try {
      if (isEdit) {
        const { subagent } = await api.subagentsUpdate(draft.id, draft)
        setNotice(`已更新「${subagent.name}」`)
      } else {
        const { subagent } = await api.subagentsCreate(draft)
        setNotice(`已创建「${subagent.name}」`)
      }
      setDraft(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (s: SubagentInfo) => {
    if (!window.confirm(`删除 subagent「${s.name}」？`)) return
    setBusy(true)
    setNotice('')
    setError('')
    try {
      await api.subagentsDelete(s.id)
      setNotice(`已删除「${s.name}」`)
      if (draft?.id === s.id) setDraft(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** Ask the main agent's LLM to design a candidate; load it into the form. */
  const plan = async () => {
    const req = planText.trim()
    if (!req) return
    setPlanning(true)
    setNotice('')
    setError('')
    try {
      const { subagent } = await api.subagentsPlan(req, false)
      // avoid clobbering an existing id on save
      const taken = new Set(items.map((s) => s.id))
      let id = subagent.id
      let n = 2
      while (taken.has(id)) id = `${subagent.id}-${n++}`
      setDraft({ ...EMPTY, ...subagent, id })
      setIsEdit(false)
      setNotice(`已生成候选配置「${subagent.name}」，检查后点保存`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPlanning(false)
    }
  }

  const toggleIn = (key: 'tools' | 'skills', value: string) => {
    setDraft((d) => {
      if (!d) return d
      const cur = d[key] ?? []
      const next = cur.includes(value)
        ? cur.filter((v) => v !== value)
        : [...cur, value]
      return { ...d, [key]: next }
    })
  }

  const toolLabel = (id: string) =>
    registry?.tools.find((t) => t.id === id)?.name ?? id

  const providerLabel = (type: string) =>
    registry?.providers.find((p) => p.type === type)?.label ?? type

  const draftModels = draft ? modelsFor(draft.providerType) : []

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>🐾 助理 / 子代理</h2>
          <span className="kb-count">
            {loading
              ? '加载中…'
              : `${items.length} 个子代理 · ${registry?.tools.length ?? 0} 个可选工具 · ${registry?.skills.length ?? 0} 个技能`}
          </span>
        </div>

        {error && <div className="note note-warn">{error}</div>}
        {notice && <div className="note note-ok">{notice}</div>}

        {/* ---- LLM 规划入口 ---- */}
        <div className="sa-planner">
          <input
            type="text"
            placeholder="描述你想要的 subagent，例如：我要一个写作 subagent"
            value={planText}
            onChange={(e) => setPlanText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void plan()
            }}
          />
          <button
            className="btn-create"
            disabled={planning || !planText.trim()}
            onClick={() => void plan()}
          >
            {planning ? '规划中…' : '让 AI 规划'}
          </button>
        </div>
        <div className="pathline muted">
          规划会把「可用模型 / 工具 / 技能」注册表交给主 agent 的 LLM，只从真实存在的资源里选。
          {registry?.mcpNote ? `（${registry.mcpNote}）` : ''}
        </div>

        {/* ---- 列表 ---- */}
        <div className="sa-grid">
          {items.map((s) => (
            <div key={s.id} className="sa-card">
              <div className="sa-card-head">
                <span className="sa-emoji">{s.emoji || '🤖'}</span>
                <div>
                  <div className="sa-name">{s.name}</div>
                  <div className="sa-id mono">{s.id}</div>
                </div>
              </div>
              <div className="sa-desc">{s.description || '（无描述）'}</div>
              <div className="sa-meta muted">
                {providerLabel(s.providerType)} · {s.model || '未选模型'} · 最多{' '}
                {s.maxRounds} 轮
              </div>
              <div className="sa-tags">
                {(s.tools ?? []).map((t) => (
                  <span key={t} className="sa-tag" title={t}>
                    {toolLabel(t)}
                  </span>
                ))}
                {(s.skills ?? []).map((k) => (
                  <span key={k} className="sa-tag sa-tag-skill">
                    ⚡ {k}
                  </span>
                ))}
                {s.tools.length === 0 && s.skills.length === 0 && (
                  <span className="sa-tag">无工具</span>
                )}
              </div>
              <div className="sa-actions">
                <button
                  className="memory-action-btn"
                  disabled={busy}
                  onClick={() => startEdit(s)}
                >
                  编辑
                </button>
                <button
                  className="memory-action-btn"
                  disabled={busy}
                  onClick={() => void remove(s)}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          {items.length === 0 && !loading && (
            <div className="kb-empty">
              还没有子代理。上面描述一句需求让 AI 规划，或点「新建」。
            </div>
          )}
        </div>

        <div className="sa-actions" style={{ marginTop: 12 }}>
          <button className="btn-create" disabled={busy} onClick={startNew}>
            ＋ 新建子代理
          </button>
        </div>

        {/* ---- 编辑 / 新建表单 ---- */}
        {draft && (
          <div className="sa-form">
            <div className="kb-item-header">
              <span className="kb-item-title">
                {isEdit ? `编辑「${draft.name || draft.id}」` : '新建子代理'}
              </span>
            </div>

            <div className="sa-row">
              <div>
                <label className="sa-label">名称</label>
                <input
                  type="text"
                  value={draft.name}
                  placeholder="写作助手"
                  onChange={(e) =>
                    setDraft({ ...draft, name: e.target.value })
                  }
                />
              </div>
              <div style={{ flex: '0 0 84px' }}>
                <label className="sa-label">图标</label>
                <input
                  type="text"
                  value={draft.emoji}
                  onChange={(e) =>
                    setDraft({ ...draft, emoji: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="sa-label">标识 id（英文）</label>
                <input
                  type="text"
                  className="mono"
                  value={draft.id}
                  disabled={isEdit}
                  placeholder="writer"
                  onChange={(e) => setDraft({ ...draft, id: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label className="sa-label">一句话职责</label>
              <input
                type="text"
                value={draft.description}
                placeholder="负责长文写作与润色"
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
              />
            </div>

            <div>
              <label className="sa-label">人设 / 系统提示（persona）</label>
              <textarea
                rows={4}
                value={draft.persona}
                placeholder="你是一名资深中文写作编辑…"
                onChange={(e) =>
                  setDraft({ ...draft, persona: e.target.value })
                }
              />
            </div>

            <div className="sa-row">
              <div>
                <label className="sa-label">模型服务</label>
                <select
                  value={draft.providerType}
                  onChange={(e) => {
                    const ptype = e.target.value
                    const p = usableProviders.find((x) => x.type === ptype)
                    setDraft({
                      ...draft,
                      providerType: ptype,
                      model: p?.model ?? '',
                    })
                  }}
                >
                  <option value="">— 选择 —</option>
                  {usableProviders.map((p) => (
                    <option key={p.type} value={p.type}>
                      {p.label}
                      {p.isActive ? '（当前）' : ''}
                    </option>
                  ))}
                </select>
                {usableProviders.length === 0 && (
                  <div className="sa-hint">
                    没有可用模型服务：请先在 设置 → 模型服务 配好 API Key。
                  </div>
                )}
              </div>
              <div>
                <label className="sa-label">模型</label>
                <input
                  type="text"
                  className="mono"
                  list="sa-model-options"
                  value={draft.model}
                  placeholder="model id"
                  onChange={(e) =>
                    setDraft({ ...draft, model: e.target.value })
                  }
                />
                <datalist id="sa-model-options">
                  {draftModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display}
                    </option>
                  ))}
                </datalist>
              </div>
              <div style={{ flex: '0 0 120px' }}>
                <label className="sa-label">最多轮数</label>
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={draft.maxRounds}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      maxRounds: Number(e.target.value) || 6,
                    })
                  }
                />
              </div>
            </div>

            <div>
              <label className="sa-label">
                工具（已选 {draft.tools.length}）
              </label>
              <div className="sa-chips">
                {(registry?.tools ?? []).map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    title={`${t.id} — ${t.description}`}
                    className={`sa-chip ${draft.tools.includes(t.id) ? 'on' : ''}`}
                    onClick={() => toggleIn('tools', t.id)}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="sa-label">
                技能（已选 {draft.skills.length}）
              </label>
              {(registry?.skills ?? []).length === 0 ? (
                <div className="sa-hint">还没有安装任何技能。</div>
              ) : (
                <div className="sa-chips">
                  {(registry?.skills ?? []).map((k) => (
                    <button
                      key={k.name}
                      type="button"
                      title={k.description}
                      className={`sa-chip ${draft.skills.includes(k.name) ? 'on' : ''}`}
                      onClick={() => toggleIn('skills', k.name)}
                    >
                      ⚡ {k.name}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="sa-actions">
              <button
                className="btn-create"
                disabled={busy || !draft.name.trim() || !draft.providerType}
                onClick={() => void save()}
              >
                {busy ? '保存中…' : isEdit ? '保存修改' : '创建'}
              </button>
              <button
                className="memory-action-btn"
                disabled={busy}
                onClick={() => setDraft(null)}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
