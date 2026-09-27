import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { SubagentInfo, SubagentRegistry, ToolInfo } from '../types'

const EMPTY: SubagentInfo = {
  id: '',
  name: '',
  emoji: '🤖',
  description: '',
  persona: '',
  providerId: '',
  model: '',
  tools: [],
  skills: [],
  mcpServers: [],
  maxRounds: 6,
}

/** 工具来源标注：ZCode 那套 / 可借用的宿主工具 / 普通应用工具。 */
function toolBadge(t: ToolInfo): string {
  if (t.source === 'zcode') {
    // 相交登记只是「功能相交」，不是别名 —— 界面上必须说清它仍是 ZCode 的实现。
    return t.intersects ? `ZCode · 相交 ${t.intersects}` : 'ZCode'
  }
  if (t.category === 'Plugin') return '插件'
  return ''
}

/**
 * 只回传**可改字段**。
 *
 * 编辑表单是用整条 ``SubagentInfo`` 填的，里面混着后端合成的只读信息
 * （``skillRoots`` / ``source`` / ``systemPrompt`` / ``borrowTools`` …）。
 * 把它们原样 PUT 回去既没有意义，还会被当成「试图修改不支持字段」而报错 ——
 * 所以这里显式挑出真正属于用户输入的那几个字段。
 */
function editablePayload(d: SubagentInfo) {
  return {
    name: d.name,
    emoji: d.emoji,
    description: d.description,
    persona: d.persona,
    providerId: d.providerId,
    model: d.model,
    tools: d.tools,
    skills: d.skills,
    mcpServers: d.mcpServers,
    maxRounds: d.maxRounds,
  }
}

/**
 * SubagentsView - 助理（子代理）管理
 *
 * 一个 subagent = 一份「人设 + 自己的模型 + 自己的工具/技能/MCP」配置，
 * 主 agent 通过 subagent_delegate 工具把任务派给它跑独立循环。
 *
 * 还有一类特殊的：**外部 agent**（ZCode、Codex…，由协议描述符接入）。它的
 * tool / mcp / skill / loop / prompt 都是自己那一套、可以独立运行，所以这里：
 *  - 可以改配置（改的是覆盖层），但不能删除（删描述符文件才是移除方式）；
 *  - 模型可以留空（表示跟随当前激活实例），CLI 类外部 agent 干脆不需要模型；
 *  - 工具列表里 ZCode 那套与可借用的宿主工具会分别标注。
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

  /** 实例 id → 该实例的协议类型（注册表 models 仍按协议分组）。 */
  const typeOf = useCallback(
    (pid: string) =>
      (registry?.providers ?? []).find((p) => p.id === pid)?.type ?? '',
    [registry],
  )

  const modelsFor = useCallback(
    (pid: string) =>
      (registry?.models ?? []).filter((m) => m.providerType === typeOf(pid)),
    [registry, typeOf],
  )

  const startNew = () => {
    const p = usableProviders[0]
    setDraft({
      ...EMPTY,
      providerId: p?.id ?? '',
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
        const { subagent } = await api.subagentsUpdate(draft.id, editablePayload(draft))
        setNotice(`已更新「${subagent.name}」`)
      } else {
        const { subagent } = await api.subagentsCreate({ id: draft.id, ...editablePayload(draft) })
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

  const toolTitle = (id: string) => {
    const t = registry?.tools.find((x) => x.id === id)
    if (!t) return id
    const badge = toolBadge(t)
    return `${t.id} — ${t.description}${badge ? `（${badge}）` : ''}`
  }

  const providerLabel = (pid: string) =>
    registry?.providers.find((p) => p.id === pid)?.label ?? (pid || '—')

  const draftModels = draft ? modelsFor(draft.providerId) : []

  // 外部 agent：模型可以留空（跟随激活实例），CLI 类甚至不需要模型。
  const draftIsExternal = Boolean(draft?.external)
  const draftTransport = draft?.transport ?? ''
  const needsModel = !draftIsExternal || draftTransport === 'internal'
  const canSave = Boolean(
    draft &&
      draft.name.trim() &&
      (!needsModel || draft.providerId.trim()),
  )

  // 工具/技能面板：外部 agent 分两栏 ——「自带的」（锁定项，**只增不减**）与
  // 「借自主智能体的」（可勾选）。两者不是一回事：前者是它本体，后者是借来的，
  // 执行时落回宿主实现与沙箱。自带的那些在界面上删不掉，后端也拒绝删除。
  const tools = registry?.tools ?? []
  const ownToolIds = new Set(draft?.ownTools ?? draft?.selfManagedTools ?? [])
  const borrowedToolIds = (draft?.tools ?? []).filter(
    (t) => !ownToolIds.has(t),
  )
  const borrowableTools = tools.filter((t) => !ownToolIds.has(t.id))
  const canBorrowTools = draft?.borrowTools !== false

  const ownSkills = draft?.ownSkills ?? []
  const ownSkillNames = new Set(ownSkills)
  const borrowedSkillNames = (draft?.skills ?? []).filter(
    (s) => !ownSkillNames.has(s),
  )
  // 可借的是**主智能体技能库**里的技能；别的外部 agent 的技能各归各的，不在这里。
  const borrowableSkills = (registry?.skills ?? []).filter(
    (k) => k.source !== 'external' && !ownSkillNames.has(k.name),
  )
  const canBorrowSkills = draft?.borrowSkills !== false

  const enabledExternal = (registry?.externalAgents ?? []).filter((a) => a.enabled)
  const reservedExternal = (registry?.externalAgents ?? []).filter(
    (a) => !a.enabled,
  )

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>🐾 助理 / 子代理</h2>
          <span className="kb-count">
            {loading
              ? '加载中…'
              : `${items.length} 个子代理（含 ${enabledExternal.length} 个外部 agent） · ${registry?.tools.length ?? 0} 个可选工具 · ${registry?.skills.length ?? 0} 个技能`}
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
                  <div className="sa-name">
                    {s.name}
                    {s.external && (
                      <span className="sa-tag" style={{ marginLeft: 6 }}>
                        外部 agent{s.transport ? ` · ${s.transport}` : ''}
                      </span>
                    )}
                    {s.builtin && !s.external && (
                      <span className="sa-tag" style={{ marginLeft: 6 }}>
                        内置
                      </span>
                    )}
                  </div>
                  <div className="sa-id mono">{s.id}</div>
                </div>
              </div>
              <div className="sa-desc">{s.description || '（无描述）'}</div>
              <div className="sa-meta muted">
                {s.external && s.transport !== 'internal'
                  ? '自带执行环境（不需要本应用的模型）'
                  : `${providerLabel(s.providerId)} · ${s.model || '未选模型'}`}{' '}
                · 最多 {s.maxRounds} 轮
              </div>
              {s.external && (
                <div className="sa-meta muted">
                  互借：
                  {s.borrowTools ? '工具' : '工具（关）'} ·{' '}
                  {s.borrowSkills ? '技能' : '技能（关）'}
                  {` · 自带 ${(s.selfManagedTools ?? []).length} 个工具`}
                  {(s.ownSkills ?? []).length > 0 &&
                    ` / ${(s.ownSkills ?? []).length} 个技能`}
                  {((s.borrowedTools ?? []).length > 0 ||
                    (s.borrowedSkills ?? []).length > 0) &&
                    ` · 已借 ${(s.borrowedTools ?? []).length} 工具 / ${
                      (s.borrowedSkills ?? []).length
                    } 技能`}
                </div>
              )}
              <div className="sa-tags">
                {(s.tools ?? []).map((t) => {
                  const borrowed =
                    s.external && (s.borrowedTools ?? []).includes(t)
                  return (
                    <span key={t} className="sa-tag" title={toolTitle(t)}>
                      {borrowed ? '借 ' : ''}
                      {toolLabel(t)}
                    </span>
                  )
                })}
                {(s.skills ?? []).map((k) => {
                  const borrowed =
                    s.external && (s.borrowedSkills ?? []).includes(k)
                  return (
                    <span key={k} className="sa-tag sa-tag-skill">
                      ⚡ {borrowed ? '借 ' : ''}
                      {k}
                    </span>
                  )
                })}
                {s.tools.length === 0 && s.skills.length === 0 && (
                  <span className="sa-tag">
                    {s.external ? '用自带工具链' : '无工具'}
                  </span>
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
                {s.external ? (
                  <span
                    className="sa-hint"
                    title={
                      s.source
                        ? `由描述符提供：${s.source}\n删掉该文件即可移除`
                        : '由协议描述符提供，不可在此删除'
                    }
                    style={{ alignSelf: 'center' }}
                  >
                    不可删除（外部 agent）
                  </span>
                ) : (
                  <button
                    className="memory-action-btn"
                    disabled={busy}
                    onClick={() => void remove(s)}
                  >
                    删除
                  </button>
                )}
              </div>
            </div>
          ))}
          {items.length === 0 && !loading && (
            <div className="kb-empty">
              还没有子代理。上面描述一句需求让 AI 规划，或点「新建」。
            </div>
          )}
        </div>

        {/* ---- 预留的外部 agent 槽位（协议文件已就位，填好命令即可启用）---- */}
        {reservedExternal.length > 0 && (
          <div className="pathline muted" style={{ marginTop: 12 }}>
            已接入的外部 agent：{enabledExternal.length} 个启用 ·{' '}
            {reservedExternal.length} 个预留槽位（
            {reservedExternal.map((a) => a.name).join(' / ')}
            ）。预留槽位不参与委派，把描述符里的 <span className="mono">enabled</span>{' '}
            改成 true 即可启用。
          </div>
        )}

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

            {draftIsExternal && (
              <div className="pathline muted">
                这是<span style={{ fontWeight: 600 }}>外部 agent</span>（
                <span className="mono">{draftTransport || 'internal'}</span>）：它的
                tool / skill / loop / prompt 都是自己那一套，可以独立运行。这里保存的是
                <span style={{ fontWeight: 600 }}>覆盖层</span>
                —— 只存改动过的字段，其余仍随描述符默认值走（描述符升级会自动生效）。
                想彻底移除它，删掉
                {draft.source ? (
                  <span className="mono"> {draft.source} </span>
                ) : (
                  ' 它的描述符文件 '
                )}
                即可。
              </div>
            )}

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
              <label className="sa-label">
                {draftIsExternal
                  ? '补充说明（追加在它自己的系统提示之后）'
                  : '人设 / 系统提示（persona）'}
              </label>
              <textarea
                rows={4}
                value={draft.persona}
                placeholder="你是一名资深中文写作编辑…"
                onChange={(e) =>
                  setDraft({ ...draft, persona: e.target.value })
                }
              />
              {draftIsExternal && draft.systemPrompt && (
                <div className="sa-hint">
                  外部 agent 的系统提示由它自己提供（描述符），这里只做追加，改不了它的
                  循环与人设内核。
                </div>
              )}
            </div>

            <div className="sa-row">
              <div>
                <label className="sa-label">
                  模型服务{needsModel ? '' : '（可留空 = 跟随当前激活实例）'}
                </label>
                <select
                  value={draft.providerId}
                  onChange={(e) => {
                    const pid = e.target.value
                    const p = usableProviders.find((x) => x.id === pid)
                    setDraft({
                      ...draft,
                      providerId: pid,
                      model: p?.model ?? '',
                    })
                  }}
                >
                  <option value="">{needsModel ? '— 选择 —' : '— 跟随当前 —'}</option>
                  {usableProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                      {p.isActive ? '（当前）' : ''}
                    </option>
                  ))}
                </select>
                {usableProviders.length === 0 && needsModel && (
                  <div className="sa-hint">
                    没有可用模型服务：请先在 设置 → 模型服务 配好 API Key。
                  </div>
                )}
                {!needsModel && (
                  <div className="sa-hint">
                    该外部 agent 自带执行环境（{draftTransport}），不需要本应用的模型服务。
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

            {draftIsExternal ? (
              <>
                <div>
                  <label className="sa-label">
                    自带工具链（{ownToolIds.size}）· 不可删除
                  </label>
                  <div className="sa-chips">
                    {[...ownToolIds].map((id) => (
                      <span
                        key={id}
                        className="sa-chip locked"
                        title={`${id} —— 它自带的工具，只增不减；要移除请改它的协议描述符`}
                      >
                        🔒 {toolLabel(id)}
                      </span>
                    ))}
                    {ownToolIds.size === 0 && (
                      <span className="sa-hint">
                        它的工具在它自己的进程里，这里没有可列举的。
                      </span>
                    )}
                  </div>
                </div>

                <div>
                  <label className="sa-label">
                    借自主智能体（已选 {borrowedToolIds.length}）
                  </label>
                  <div className="sa-hint">
                    {canBorrowTools
                      ? '勾选即「借用」主智能体的工具：它仍然用自己的工具链，这些借来的工具执行时落回宿主实现与沙箱。自带的那套删不掉，这里只是纯增量。'
                      : '这个 agent 在描述符里关掉了工具借用（borrow.tools: false），勾了也不会生效。'}
                  </div>
                  <div className="sa-chips">
                    {borrowableTools.map((t) => {
                      const badge = toolBadge(t)
                      return (
                        <button
                          key={t.id}
                          type="button"
                          disabled={!canBorrowTools}
                          title={
                            canBorrowTools
                              ? toolTitle(t.id)
                              : '该 agent 已关闭工具借用'
                          }
                          className={`sa-chip ${draft.tools.includes(t.id) ? 'on' : ''}`}
                          onClick={() => toggleIn('tools', t.id)}
                        >
                          {t.name}
                          <span className="sa-chip-badge"> 借</span>
                          {badge && <span className="sa-chip-badge"> {badge}</span>}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </>
            ) : (
              <div>
                <label className="sa-label">
                  工具（已选 {draft.tools.length}）
                </label>
                <div className="sa-chips">
                  {tools.map((t) => {
                    const badge = toolBadge(t)
                    return (
                      <button
                        key={t.id}
                        type="button"
                        title={toolTitle(t.id)}
                        className={`sa-chip ${draft.tools.includes(t.id) ? 'on' : ''}`}
                        onClick={() => toggleIn('tools', t.id)}
                      >
                        {t.name}
                        {badge && <span className="sa-chip-badge"> {badge}</span>}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}

            {draftIsExternal ? (
              <>
                <div>
                  <label className="sa-label">
                    自带技能（{ownSkills.length}）· 不可删除
                  </label>
                  <div className="sa-chips">
                    {ownSkills.map((name) => (
                      <span
                        key={name}
                        className="sa-chip locked"
                        title={`${name} —— 它自己技能根（${(draft.skillRoots ?? []).join(' / ') || '未声明'}）里的技能，只增不减`}
                      >
                        🔒 ⚡ {name}
                      </span>
                    ))}
                    {ownSkills.length === 0 && (
                      <span className="sa-hint">
                        {draft.skillRoots?.length
                          ? '它的技能根里暂时没有技能 —— 往那些目录里放 SKILL.md 就会出现在这里。'
                          : '它没有声明技能根（描述符里的 skills.roots）。'}
                      </span>
                    )}
                  </div>
                </div>

                <div>
                  <label className="sa-label">
                    借自主智能体（已选 {borrowedSkillNames.length}）
                  </label>
                  <div className="sa-hint">
                    {!canBorrowSkills
                      ? '这个 agent 在描述符里关掉了技能借用（borrow.skills: false），勾了也不会生效。'
                      : borrowedSkillNames.length
                        ? '只借勾中的这几个：它自己的技能照旧可用，多出来的这些来自主智能体技能库。'
                        : '一个都没勾 = 借整个主智能体技能库（默认行为）；勾上就是只借这几个。'}
                  </div>
                  {borrowableSkills.length === 0 ? (
                    <div className="sa-hint">主智能体技能库里还没有可借的技能。</div>
                  ) : (
                    <div className="sa-chips">
                      {borrowableSkills.map((k) => (
                        <button
                          key={`${k.source ?? 'host'}:${k.name}`}
                          type="button"
                          disabled={!canBorrowSkills}
                          title={
                            `${k.description}` +
                            (k.path ? `\n${k.path}` : '') +
                            (k.shadowed
                              ? '\n（与它自己的同名技能并存，两边正文可能不同）'
                              : '')
                          }
                          className={`sa-chip ${draft.skills.includes(k.name) ? 'on' : ''}`}
                          onClick={() => toggleIn('skills', k.name)}
                        >
                          ⚡ {k.name}
                          <span className="sa-chip-badge"> 借</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
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
                        key={`${k.source ?? 'host'}:${k.name}`}
                        type="button"
                        title={
                          `${k.description}` +
                          (k.source === 'external'
                            ? `\n来源：外部 agent ${k.agentId ?? ''} 自己的技能根`
                            : '') +
                          (k.path ? `\n${k.path}` : '') +
                          (k.shadowed
                            ? '\n（与主智能体的同名技能并存，不做合并）'
                            : '')
                        }
                        className={`sa-chip ${draft.skills.includes(k.name) ? 'on' : ''}`}
                        onClick={() => toggleIn('skills', k.name)}
                      >
                        ⚡ {k.name}
                        {k.source === 'external' && (
                          <span className="sa-chip-badge"> 外部</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="sa-actions">
              <button
                className="btn-create"
                disabled={busy || !canSave}
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
