/** Settings detail pages for the model & agent side of the tree:
 * 模型服务 / 身份与人格 / 灵魂(Soul) / 技能与工具 / Token 用量 / 记忆.
 * Shared edits stage locally and commit with one Save — the store accepts
 * partial payloads so each page only sends what it changed.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'
import type {
  Attribution,
  CustomIdentityDraft,
  MemoryFileInfo,
  ProviderInfo,
  SettingsInfo,
  UsageModelStats,
} from '../../types'
import { DetailShell, Note, SectionCard, Spinner, StatusLine, useAsync } from './ui'
import type { SetPageId } from './entries'

// ---------------------------------------------------------------------------
// shared bits
// ---------------------------------------------------------------------------
function mergeModels(builtin: { id: string; name: string }[], remote: string[]) {
  const known = new Set(builtin.map((m) => m.id))
  const out = [...builtin]
  for (const id of remote) {
    if (!known.has(id)) {
      out.push({ id, name: `${id}(远程)` })
      known.add(id)
    }
  }
  return out
}

const shortUrl = (u: string) =>
  u.replace(/^https?:\/\//, '').replace(/\/$/, '').slice(0, 56)

interface FetchMsg {
  ok: boolean
  text: string
}

// ---------------------------------------------------------------------------
// 模型服务 — 厂商实例(同协议可多家) / API Key / Base URL / 默认模型 / 设为当前
// ---------------------------------------------------------------------------
/** 一行的可编辑状态:已保存实例 + 尚未落盘的新实例。 */
interface ProviderRow {
  id: string
  type: string
  label: string
  typeLabel: string
  apiKey: string // draft only; '' = 保留原 Key
  baseUrl: string
  model: string
  hasKey: boolean
  engine: string | null
  note: string
  defaultModel: string
  models: { id: string; name: string }[]
  saved: boolean // false = 本次新增,还没 POST 过
}

const rowFrom = (p: ProviderInfo): ProviderRow => ({
  id: p.id, type: p.type, label: p.label, typeLabel: p.typeLabel,
  apiKey: '', baseUrl: p.baseUrl, model: p.model, hasKey: p.hasKey,
  engine: p.engine, note: p.note, defaultModel: p.defaultModel,
  models: [...p.models], saved: true,
})

export function ProvidersPage(props: { onBack: () => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [activeProvider, setActiveProvider] = useState<string | null>(null)
  const [rows, setRows] = useState<ProviderRow[]>([])
  const [fetchBusy, setFetchBusy] = useState<Record<string, boolean>>({})
  const [fetchMsg, setFetchMsg] = useState<Record<string, FetchMsg | undefined>>({})
  const [newType, setNewType] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const s = await api.settingsGet()
      setSettings(s)
      setActiveProvider(s.activeProviderId)
      setRows(s.providers.map(rowFrom))
      setNewType(s.providerTypes[0]?.type ?? '')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const save = useCallback(async () => {
    setSaving(true)
    setError(null)
    setSaved(null)
    try {
      const providers = rows.map((r) => ({
        id: r.id,
        type: r.type,
        label: r.label,
        apiKey: r.apiKey,
        baseUrl: r.baseUrl,
        model: r.model,
      }))
      const s = await api.settingsPut({ activeProviderId: activeProvider, providers })
      setSettings(s)
      setRows(s.providers.map(rowFrom))
      setSaved('已保存 ✓')
      setTimeout(() => setSaved(null), 2500)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [rows, activeProvider])

  const patch = (id: string, p: Partial<ProviderRow>) =>
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...p } : r)))

  const nextId = (type: string) => {
    const used = new Set(rows.map((r) => r.id))
    if (!used.has(type)) return type
    let n = 2
    while (used.has(`${type}-${n}`)) n += 1
    return `${type}-${n}`
  }

  const addProvider = () => {
    const t = settings?.providerTypes.find((x) => x.type === newType)
    if (!t) return
    setRows((prev) => [
      ...prev,
      {
        id: nextId(t.type), type: t.type, label: t.label, typeLabel: t.label,
        apiKey: '', baseUrl: '', model: t.defaultModel, hasKey: false,
        engine: t.engine, note: t.note, defaultModel: t.defaultModel,
        models: [], saved: false,
      },
    ])
    setError(null)
  }

  const removeProvider = (id: string) => {
    if (!window.confirm(`删除厂商实例「${id}」?`)) return
    setRows((prev) => prev.filter((r) => r.id !== id))
    if (activeProvider === id) setActiveProvider(null)
  }

  const fetchRemote = useCallback(
    async (r: ProviderRow) => {
      const baseUrl = r.baseUrl.trim()
      if (!baseUrl) {
        setFetchMsg((m) => ({ ...m, [r.id]: { ok: false, text: '请先填写 Base URL,再获取模型列表。' } }))
        return
      }
      setFetchBusy((b) => ({ ...b, [r.id]: true }))
      setFetchMsg((m) => ({ ...m, [r.id]: undefined }))
      try {
        const res = await api.fetchModels({
          id: r.saved ? r.id : undefined,
          type: r.type,
          baseUrl,
          apiKey: r.apiKey.trim(),
        })
        setRows((prev) =>
          prev.map((x) => (x.id === r.id ? { ...x, models: mergeModels(x.models, res.models) } : x)),
        )
        setFetchMsg((m) => ({
          ...m,
          [r.id]: { ok: true, text: `从 ${shortUrl(res.source)} 获取到 ${res.models.length} 个模型。` },
        }))
      } catch (e) {
        setFetchMsg((m) => ({ ...m, [r.id]: { ok: false, text: (e as Error).message } }))
      } finally {
        setFetchBusy((b) => ({ ...b, [r.id]: false }))
      }
    },
    [],
  )

  if (loading) return <DetailShell title="模型服务" onBack={props.onBack}><Spinner /></DetailShell>

  const types = settings?.providerTypes ?? []
  const current = rows.find((r) => r.id === activeProvider)

  return (
    <DetailShell
      title="模型服务"
      subtitle="同一协议(如 OpenAI 兼容)可添加多家厂商实例,各自独立的 Key / Base URL / 模型。Key 只保存在本机 settings.json;留空表示保留原 Key。"
      onBack={props.onBack}
      extra={
        <>
          <StatusLine ok={saved} err={error} />
          <button onClick={() => void save()} disabled={saving}>
            {saving ? 'Saving…' : '保存'}
          </button>
        </>
      }
    >
      {current && (
        <p className="statusline">
          当前对话使用:{' '}
          <strong>
            {current.label || current.typeLabel} · {current.model || '未设模型'}
          </strong>
          <span className="muted"> ({current.id})</span>
        </p>
      )}
      {rows.length === 0 && (
        <p className="empty">还没有配置任何厂商。从下方「添加厂商」选一个协议开始。</p>
      )}

      <div className="provider-list">
        {rows.map((r) => {
          const isActive = activeProvider === r.id
          const ready = !!r.engine
          const canActivate = ready && (r.apiKey || r.hasKey)
          return (
            <div key={r.id} className={`provider-card ${isActive ? 'active' : ''}`}>
              <div className="provider-head">
                <div className="provider-title">
                  <input
                    className="provider-label-input"
                    value={r.label}
                    placeholder={r.typeLabel}
                    onChange={(e) => patch(r.id, { label: e.target.value })}
                  />
                  <span className="pill soft">{r.typeLabel}</span>
                  {ready ? <span className="pill ok">引擎就绪</span> : <span className="pill warn">{r.note}</span>}
                  {isActive && <span className="pill current">当前</span>}
                  {!r.saved && <span className="pill warn">未保存</span>}
                </div>
                <div className="provider-actions">
                  <button
                    className="link"
                    disabled={!canActivate}
                    onClick={() => setActiveProvider(r.id)}
                  >
                    设为当前
                  </button>
                  <button className="link danger" onClick={() => removeProvider(r.id)}>
                    删除
                  </button>
                </div>
              </div>
              <p className="muted pathline">id: {r.id}</p>
              <div className="provider-fields">
                <label>
                  API Key
                  <input
                    type="password"
                    value={r.apiKey}
                    placeholder={r.hasKey ? '•••••••• (已配置,留空不变)' : 'sk-…'}
                    onChange={(e) => patch(r.id, { apiKey: e.target.value })}
                  />
                </label>
                <label>
                  <span className="field-row">
                    模型
                    <button type="button" className="link" disabled={!!fetchBusy[r.id]} onClick={() => void fetchRemote(r)}>
                      {fetchBusy[r.id] ? '获取中…' : '从 URL 获取列表'}
                    </button>
                  </span>
                  <input
                    list={`models-${r.id}`}
                    value={r.model}
                    placeholder={r.defaultModel || '手动输入模型 ID'}
                    onChange={(e) => patch(r.id, { model: e.target.value })}
                  />
                  <datalist id={`models-${r.id}`} key={`${r.id}-${r.models.length}`}>
                    {r.models.map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </datalist>
                </label>
                <label className="wide">
                  Base URL (可选)
                  <input
                    value={r.baseUrl}
                    placeholder={r.engine === 'openai' ? 'https://api.openai.com/v1' : '官方地址或代理'}
                    onChange={(e) => patch(r.id, { baseUrl: e.target.value })}
                  />
                </label>
              </div>
              {r.models.some((m) => m.name.endsWith('(远程)')) && (
                <div className="remote-models">
                  <span className="muted">远程模型(点击选用):</span>
                  <div className="tool-chips">
                    {r.models
                      .filter((m) => m.name.endsWith('(远程)'))
                      .map((m) => (
                        <button type="button" key={m.id} className="chip on" onClick={() => patch(r.id, { model: m.id })}>
                          {m.id}
                        </button>
                      ))}
                  </div>
                </div>
              )}
              {fetchMsg[r.id] && <div className={`fetch-msg ${fetchMsg[r.id]?.ok ? 'ok' : 'bad'}`}>{fetchMsg[r.id]?.text}</div>}
            </div>
          )
        })}
      </div>

      <SectionCard
        title="添加厂商"
        hint="同一协议可添加多个实例(例如两个 OpenAI 兼容网关),各自独立配置。"
      >
        <div className="row-actions">
          <select value={newType} onChange={(e) => setNewType(e.target.value)}>
            {types.map((t) => (
              <option key={t.type} value={t.type}>
                {t.label}{t.engine ? '' : ` · ${t.note}`}
              </option>
            ))}
          </select>
          <button onClick={addProvider} disabled={!newType}>＋ 添加</button>
        </div>
      </SectionCard>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 身份与人格 — roles / persona / tool toggles / custom identities
// ---------------------------------------------------------------------------
export function IdentitiesPage(props: { onBack: () => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [activeId, setActiveId] = useState('')
  const [staged, setStaged] = useState<Record<string, string[]>>({})
  const [customs, setCustoms] = useState<CustomIdentityDraft[]>([])
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<CustomIdentityDraft>({
    id: '', name: '', emoji: '🧩', description: '', persona: '', enabledTools: [],
  })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const s = await api.settingsGet()
      setSettings(s)
      setActiveId(s.activeIdentityId)
      const st: Record<string, string[]> = {}
      for (const i of s.identities) st[i.id] = [...i.enabledTools]
      setStaged(st)
      setCustoms(
        s.identities
          .filter((i) => !i.builtin)
          .map((i) => ({
            id: i.id, name: i.name, emoji: i.emoji, description: i.description,
            persona: i.persona, enabledTools: [...i.enabledTools],
          })),
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const save = useCallback(async () => {
    if (!settings) return
    setSaving(true)
    setError(null)
    setSaved(null)
    try {
      const identityEdits = settings.identities.map((i) => ({
        id: i.id,
        enabledTools: staged[i.id] ?? i.enabledTools,
      }))
      const s = await api.settingsPut({
        activeIdentityId: activeId,
        identityEdits,
        customIdentities: customs,
      })
      setSettings(s)
      const st: Record<string, string[]> = {}
      for (const i of s.identities) st[i.id] = [...i.enabledTools]
      setStaged(st)
      setCustoms(
        s.identities
          .filter((i) => !i.builtin)
          .map((i) => ({ id: i.id, name: i.name, emoji: i.emoji, description: i.description, persona: i.persona, enabledTools: [...i.enabledTools] })),
      )
      setSaved('已保存 ✓')
      setTimeout(() => setSaved(null), 2500)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [settings, staged, activeId, customs])

  const tools = settings?.toolCatalog ?? []
  const selected = settings?.identities.find((i) => i.id === activeId)

  const toggleTool = (id: string, toolId: string) => {
    const cur = staged[id] ?? []
    const next = cur.includes(toolId) ? cur.filter((t) => t !== toolId) : [...cur, toolId]
    setStaged((prev) => ({ ...prev, [id]: next }))
  }

  const patchCustom = (patch: Partial<CustomIdentityDraft>) =>
    setDraft((d) => ({ ...d, ...patch }))

  const addCustom = () => {
    const id = draft.id.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
    if (!id || !draft.name.trim() || !draft.persona.trim()) {
      setError('请填写 ID、名称与 persona。')
      return
    }
    const taken = new Set([...(settings?.identities ?? []).map((i) => i.id), ...customs.map((c) => c.id)])
    if (taken.has(id)) {
      setError(`身份 id 已存在: ${id}`)
      return
    }
    const custom = { ...draft, id, enabledTools: draft.enabledTools?.length ? draft.enabledTools : undefined }
    setCustoms((prev) => [...prev, custom])
    setStaged((prev) => ({ ...prev, [id]: custom.enabledTools ?? [] }))
    setActiveId(id)
    setDraft({ id: '', name: '', emoji: '🧩', description: '', persona: '', enabledTools: [] })
    setCreating(false)
    setError(null)
  }

  const removeCustom = (id: string) => {
    if (!window.confirm(`删除自定义身份「${id}」?`)) return
    setCustoms((prev) => prev.filter((c) => c.id !== id))
    setStaged((prev) => {
      const next = { ...prev }
      delete next[id]
      return next
    })
    if (activeId === id) setActiveId(settings?.identities.find((i) => i.builtin)?.id ?? 'assistant')
  }

  const customFor = (id: string) => customs.find((c) => c.id === id)

  if (loading) return <DetailShell title="身份与人格" onBack={props.onBack}><Spinner /></DetailShell>

  const identities = settings?.identities ?? []
  return (
    <DetailShell
      title="身份与人格"
      subtitle="身份决定 Agent 的人设与可用技能。切换身份后 AI 按人设行事;技能可手动勾选覆盖推荐,新建自定义身份用于个性化人格。"
      onBack={props.onBack}
      extra={
        <>
          <StatusLine ok={saved} err={error} />
          <button onClick={() => void save()} disabled={saving || !settings}>
            {saving ? 'Saving…' : '保存'}
          </button>
        </>
      }
    >
      <div className="identity-grid">
        {identities.map((idn) => (
          <button
            key={idn.id}
            className={`identity-card ${idn.id === activeId ? 'active' : ''}`}
            onClick={() => setActiveId(idn.id)}
            title={idn.description}
          >
            <span className="id-emoji">{idn.emoji}</span>
            <span className="id-name">{idn.name}</span>
            {!idn.builtin && <span className="pill">自定义</span>}
            {idn.id === activeId && <span className="pill current">当前</span>}
          </button>
        ))}
      </div>

      {selected && (
        <SectionCard>
          <div className="identity-edit">
            <div className="identity-edit-head">
              <span className="id-emoji">{selected.emoji}</span>
              <strong>{selected.name}</strong>
              {!selected.builtin && (
                <button className="link danger" onClick={() => removeCustom(selected.id)}>
                  删除
                </button>
              )}
              <span className="muted desc">{selected.description}</span>
            </div>

            {selected.builtin ? (
              <details>
                <summary className="muted">人设(System prompt)</summary>
                <p className="mono persona">{selected.persona}</p>
              </details>
            ) : (
              <div className="custom-edit">
                <label>名称
                  <input value={customFor(selected.id)?.name ?? selected.name}
                    onChange={(e) => setCustoms((prev) => prev.map((c) => (c.id === selected.id ? { ...c, name: e.target.value } : c)))} />
                </label>
                <label>Emoji
                  <input value={customFor(selected.id)?.emoji ?? selected.emoji}
                    onChange={(e) => setCustoms((prev) => prev.map((c) => (c.id === selected.id ? { ...c, emoji: e.target.value } : c)))} />
                </label>
                <label>简介
                  <input value={customFor(selected.id)?.description ?? selected.description}
                    onChange={(e) => setCustoms((prev) => prev.map((c) => (c.id === selected.id ? { ...c, description: e.target.value } : c)))} />
                </label>
                <label>人设 persona
                  <textarea rows={5} className="mono"
                    value={customFor(selected.id)?.persona ?? selected.persona}
                    onChange={(e) => setCustoms((prev) => prev.map((c) => (c.id === selected.id ? { ...c, persona: e.target.value } : c)))} />
                </label>
              </div>
            )}

            <div className="tools-block">
              <span className="muted label">
                已启用技能 / 工具
                {(selected.recommendedTools?.length ?? 0) > 0 && (
                  <button className="link" onClick={() => setStaged((prev) => ({ ...prev, [selected.id]: [...selected.recommendedTools] }))}>
                    按推荐恢复
                  </button>
                )}
              </span>
              <div className="tool-chips">
                {tools.map((t) => {
                  const on = (staged[selected.id] ?? []).includes(t.id)
                  return (
                    <label key={t.id} className={`chip ${on ? 'on' : ''}`} title={t.description}>
                      <input type="checkbox" checked={on} onChange={() => toggleTool(selected.id, t.id)} />
                      {t.name}
                    </label>
                  )
                })}
              </div>
            </div>
          </div>
        </SectionCard>
      )}

      <SectionCard
        title="新建自定义身份"
        hint="给人格取个唯一 ID(字母数字与 - _),写下 persona 后即可在列表中使用。"
      >
        {!creating ? (
          <button className="link" onClick={() => setCreating(true)}>＋ 新建身份</button>
        ) : (
          <div className="custom-edit">
            <div className="custom-row">
              <label>ID
                <input value={draft.id} placeholder="如 researcher"
                  onChange={(e) => patchCustom({ id: e.target.value })} />
              </label>
              <label>名称
                <input value={draft.name} placeholder="如 研究员"
                  onChange={(e) => patchCustom({ name: e.target.value })} />
              </label>
              <label>Emoji
                <input value={draft.emoji} onChange={(e) => patchCustom({ emoji: e.target.value })} />
              </label>
            </div>
            <label>简介
              <input value={draft.description} placeholder="一句话说明这个身份的定位"
                onChange={(e) => patchCustom({ description: e.target.value })} />
            </label>
            <label>人设 persona
              <textarea rows={5} className="mono" value={draft.persona} placeholder="写清身份目标、行为准则、语气…"
                onChange={(e) => patchCustom({ persona: e.target.value })} />
            </label>
            <div className="tools-block">
              <span className="muted label">默认技能(可稍后在保存后再调)</span>
              <div className="tool-chips">
                {tools.map((t) => {
                  const on = (draft.enabledTools ?? []).includes(t.id)
                  return (
                    <label key={t.id} className={`chip ${on ? 'on' : ''}`} title={t.description}>
                      <input type="checkbox" checked={on}
                        onChange={() => {
                          const cur = draft.enabledTools ?? []
                          const next = on ? cur.filter((x) => x !== t.id) : [...cur, t.id]
                          patchCustom({ enabledTools: next })
                        }} />
                      {t.name}
                    </label>
                  )
                })}
              </div>
            </div>
            <div className="row-actions">
              <button className="btn-soft" onClick={() => setCreating(false)}>取消</button>
              <button onClick={addCustom}>创建并保存</button>
            </div>
          </div>
        )}
      </SectionCard>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 灵魂 (Soul) — persona drives behaviour + memory; 对 Android SoulStore 的最小对应
// ---------------------------------------------------------------------------
export function SoulPage(props: { onBack: () => void; go: (id: SetPageId) => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async () => {
    try {
      setSettings(await api.settingsGet())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoaded(true)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const activate = async (id: string) => {
    if (!settings || id === settings.activeIdentityId) return
    setSaving(true)
    setError(null)
    try {
      const s = await api.settingsPut({ activeIdentityId: id })
      setSettings(s)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) return <DetailShell title="灵魂 (Soul)" onBack={props.onBack}><Spinner /></DetailShell>
  const identities = settings?.identities ?? []
  const active = identities.find((i) => i.id === settings?.activeIdentityId)

  return (
    <DetailShell
      title="灵魂 (Soul)"
      subtitle="对应 Android 的 SoulSettingsScreen:人格(persona)定义 Agent 的行为与记忆方式。当前移植由「身份」携带 persona,选中的身份即当前激活的灵魂。"
      onBack={props.onBack}
    >
      <StatusLine err={error} />
      <SectionCard title="选择灵魂" hint={saving ? '保存中…' : '点击即切换,即时生效'}>
        <div className="identity-grid">
          {identities.map((idn) => (
            <button
              key={idn.id}
              className={`identity-card ${idn.id === settings?.activeIdentityId ? 'active' : ''}`}
              onClick={() => void activate(idn.id)}
            >
              <span className="id-emoji">{idn.emoji}</span>
              <span className="id-name">{idn.name}</span>
              <span className="muted desc">{idn.description}</span>
              {idn.id === settings?.activeIdentityId && <span className="pill current">激活</span>}
            </button>
          ))}
        </div>
      </SectionCard>

      {active && (
        <SectionCard title={`当前人格 · ${active.name}`} hint="这段 System prompt 会注入每一轮对话,决定行为与语气。">
          <p className="mono persona">{active.persona}</p>
          <p className="muted">
            长期记忆独立于人格保存。让 Agent 记住偏好或项目约定,请使用{' '}
            <button className="link" onClick={() => props.go('memory')}>记忆管理</button>;
            修改人格细节与工具集请前往{' '}
            <button className="link" onClick={() => props.go('identities')}>身份与人格</button>。
          </p>
        </SectionCard>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 技能与工具 — 已移植工具目录(只读概览)
// ---------------------------------------------------------------------------
const CATEGORY_ORDER = ['Shell', 'Files', 'Vision', 'Browser', 'Memory']

export function SkillsPage(props: { onBack: () => void; go: (id: SetPageId) => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    api
      .settingsGet()
      .then(setSettings)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoaded(true))
  }, [])

  if (!loaded) return <DetailShell title="技能与工具" onBack={props.onBack}><Spinner /></DetailShell>
  if (error) return <DetailShell title="技能与工具" onBack={props.onBack}><p className="error">{error}</p></DetailShell>

  const tools = settings?.toolCatalog ?? []
  const activeId = settings?.activeIdentityId
  const activeTools = new Set(settings?.identities.find((i) => i.id === activeId)?.enabledTools ?? [])
  const cats = CATEGORY_ORDER.filter((c) => tools.some((t) => t.category === c))
  const extra = tools.filter((t) => !CATEGORY_ORDER.includes(t.category ?? ''))

  return (
    <DetailShell
      title="技能与工具"
      subtitle="Agent 可调用的技能目录(Python 已移植的全部工具)。某身份的具体启用集合在其勾选页管理。"
      onBack={props.onBack}
    >
      {cats.map((cat) => (
        <SectionCard key={cat} title={cat} hint={`${tools.filter((t) => t.category === cat).length} 个工具`}>
          {tools
            .filter((t) => t.category === cat)
            .map((t) => (
              <div key={t.id} className="skill-row">
                <span className={`skill-dot ${activeTools.has(t.id) ? 'on' : ''}`} />
                <div className="skill-text">
                  <strong>{t.name}</strong>
                  <span className="muted">{t.description}</span>
                </div>
                <code>{t.id}</code>
              </div>
            ))}
        </SectionCard>
      ))}
      {extra.length > 0 && (
        <SectionCard title="其它">
          {extra.map((t) => (
            <div key={t.id} className="skill-row">
              <span className={`skill-dot ${activeTools.has(t.id) ? 'on' : ''}`} />
              <div className="skill-text">
                <strong>{t.name}</strong>
                <span className="muted">{t.description}</span>
              </div>
              <code>{t.id}</code>
            </div>
          ))}
        </SectionCard>
      )}
      <p className="muted">
        绿点 = 当前身份已启用。调整启用集合:{' '}
        <button className="link" onClick={() => props.go('identities')}>身份与人格 → 已启用技能</button>
      </p>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// Token 用量
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 对话参数 (AgentConfig) — 上下文预算 / 记忆轮次 / 工具步数 / 深度思考
// ---------------------------------------------------------------------------
export function AgentConfigPage(props: { onBack: () => void }) {
  const [cfg, setCfg] = useState<{
    maxContextTokens: number
    maxMemoryRounds: number
    maxToolSteps: number
    deepThinking: boolean
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .settingsGet()
      .then((s) => {
        if (alive) setCfg({ ...s.agent })
      })
      .catch((e) => {
        if (alive) setError((e as Error).message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  const save = async () => {
    if (!cfg) return
    setSaving(true)
    setError(null)
    setSaved(null)
    try {
      await api.settingsPut({ agent: cfg })
      setSaved('已保存 ✓')
      setTimeout(() => setSaved(null), 2000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <DetailShell title="对话参数" onBack={props.onBack}>
        <Spinner text="加载设置…" />
      </DetailShell>
    )
  }

  return (
    <DetailShell
      title="对话参数"
      subtitle="控制 Agent 单次对话的上下文预算、压缩时机与工具步数上限。"
      onBack={props.onBack}
    >
      {error && <Note kind="warn">{error}</Note>}
      {saved && <Note kind="ok">{saved}</Note>}
      {cfg && (
        <>
          <SectionCard title="上下文与压缩" hint="超过上限后会自动把旧对话压缩为摘要,历史数据不丢。">
            <div className="provider-fields">
              <label className="wide">
                <span>最大上下文 Token</span>
                <input
                  type="number"
                  min={1024}
                  step={1024}
                  value={cfg.maxContextTokens}
                  onChange={(e) =>
                    setCfg({ ...cfg, maxContextTokens: Number(e.target.value) || 0 })
                  }
                />
                <span className="muted" style={{ fontSize: 12 }}>
                  对话历史接近该预算时智能压缩(约 80% 触发)
                </span>
              </label>
              <label className="wide">
                <span>最大记忆轮次</span>
                <input
                  type="number"
                  min={1}
                  value={cfg.maxMemoryRounds}
                  onChange={(e) =>
                    setCfg({ ...cfg, maxMemoryRounds: Number(e.target.value) || 1 })
                  }
                />
                <span className="muted" style={{ fontSize: 12 }}>
                  一问一答为一轮,超过后会智能压缩处理
                </span>
              </label>
            </div>
          </SectionCard>

          <SectionCard title="执行控制">
            <div className="provider-fields">
              <label className="wide">
                <span>最大执行步数</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={cfg.maxToolSteps}
                  onChange={(e) =>
                    setCfg({ ...cfg, maxToolSteps: Number(e.target.value) || 1 })
                  }
                />
                <span className="muted" style={{ fontSize: 12 }}>
                  单次对话中 Agent 最多调用工具的次数
                </span>
              </label>
              <div
                className="cfg-row"
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' }}
              >
                <label className="skill-toggle" title="深度思考开关">
                  <input
                    type="checkbox"
                    checked={cfg.deepThinking}
                    onChange={(e) => setCfg({ ...cfg, deepThinking: e.target.checked })}
                  />
                  <span className="skill-toggle-track" />
                </label>
                <div>
                  <div style={{ fontWeight: 600 }}>深度思考</div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    开启后使用 HIGH 思考档位;推理型任务更稳、速度略慢
                  </div>
                </div>
              </div>
            </div>
          </SectionCard>

          <div style={{ marginTop: 14 }}>
            <button className="btn-create" disabled={saving} onClick={() => void save()}>
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// Token 用量 (UsageStats)
//
// 对应 Android 的 UsageStatsScreen。数据来自 /api/usage,背后是
// ChatDao.allUsageRecords() 的 LEFT JOIN:凡是带 token_usage 的消息都计入,
// 哪怕它的 sessions 行已经没了(那些 token 是真花掉的)。
//
// 这里唯一不能含糊的是「归因可信度」。原来用量是按 sessions.model_id 这个
// 可变列 join 出来的,而它每次切换模型都会被重写、且没有时间维度 —— 于是
// 一段历史的 token 会整体漂移到当前模型名下。所以带快照的行(实测)和不带
// 快照的行(估算)必须分桶显示,绝不能合并。
// ---------------------------------------------------------------------------
function fmtCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) {
    const k = n / 1000
    return Number.isInteger(k) ? `${k}k` : `${k.toFixed(1)}k`
  }
  return String(n)
}

/** 只有用户能据以行动的两态才给一句说明。
 *
 * ESTIMATED 故意什么都不显示:它是绝大多数行(快照列出现之前写入的全部历史),
 * 在几乎每一行下面重复同一句话纯属噪音 —— 曾被专门提过。区分仍然存在于内部:
 * 估算行始终留在自己的桶里,不会并进实测行,所以数字依然是诚实的。 */
function AttributionCaveat({ a }: { a: Attribution }) {
  if (a === 'UNKNOWN_SESSION') {
    return <span className="usage-caveat">会话已删除,模型未知</span>
  }
  if (a === 'MEASURED_REMOVED') {
    return <span className="usage-caveat">该模型已从配置中移除</span>
  }
  return null
}

function UsageModelRow(props: { model: UsageModelStats }) {
  const [open, setOpen] = useState(false)
  const m = props.model
  const io = m.inputTokens + m.outputTokens
  const rate =
    m.totalInput > 0 && m.cacheReadTokens > 0
      ? ((m.cacheReadTokens / m.totalInput) * 100).toFixed(1)
      : null

  const rows: [string, string][] = [
    ['输入', fmtCount(m.inputTokens)],
    ['输出', fmtCount(m.outputTokens)],
  ]
  if (m.cacheReadTokens > 0) rows.push(['缓存读取', fmtCount(m.cacheReadTokens)])
  if (m.cacheCreationTokens > 0) rows.push(['缓存写入', fmtCount(m.cacheCreationTokens)])
  if (rate) rows.push(['缓存命中率', `${rate}%`])
  if (m.activeDays > 0) rows.push(['日均', fmtCount(Math.floor(io / m.activeDays))])
  if (m.sessions > 0) rows.push(['会话均', fmtCount(Math.floor(io / m.sessions))])
  rows.push(['会话数', String(m.sessions)])
  rows.push(['活跃天数', String(m.activeDays)])

  return (
    <div className="usage-model">
      <div className="usage-row" onClick={() => setOpen((v) => !v)}>
        <div className="usage-name">
          <span>{m.displayName}</span>
          <AttributionCaveat a={m.attribution} />
        </div>
        <div className="usage-summary">
          <span className="muted">
            {m.formattedInput} / {m.formattedOutput}
          </span>
          <span className="usage-chevron">{open ? '⌄' : '›'}</span>
        </div>
      </div>
      {open && (
        <dl className="facts usage-detail">
          {rows.flatMap(([k, v], i) => [
            <dt key={`k${i}`}>{k}</dt>,
            <dd key={`v${i}`}>{v}</dd>,
          ])}
        </dl>
      )}
    </div>
  )
}

export function UsagePage(props: { onBack: () => void }) {
  const { data, error, loading, reload } = useAsync(() => api.usageStats(), [])

  const total = data?.grandTotal
  const hasData = !!data && (data.bucketCount ?? 0) > 0

  return (
    <DetailShell
      title="Token 用量"
      subtitle="按厂商与模型汇总的历史 token 消耗。"
      onBack={props.onBack}
      extra={
        <button className="link" onClick={reload}>
          刷新
        </button>
      }
    >
      {loading && <Spinner text="读取用量…" />}
      {error && <Note kind="warn">{error}</Note>}
      {data?.error && <Note kind="warn">{data.error}</Note>}

      {!loading && !error && total && (
        <>
          <SectionCard title="总计">
            <dl className="facts">
              <dt>总输入</dt>
              <dd>{total.formattedInput}</dd>
              <dt>输出</dt>
              <dd>{total.formattedOutput}</dd>
              {total.cacheReadTokens > 0 && (
                <>
                  <dt>缓存读取</dt>
                  <dd>{total.formattedCacheRead}</dd>
                </>
              )}
              {total.cacheCreationTokens > 0 && (
                <>
                  <dt>缓存写入</dt>
                  <dd>{total.formattedCacheCreation}</dd>
                </>
              )}
              {total.cacheHitRate !== null && (
                <>
                  <dt>缓存命中率</dt>
                  <dd>{total.cacheHitRate.toFixed(1)}%</dd>
                </>
              )}
            </dl>
          </SectionCard>

          {!hasData && (
            <Note kind="info">
              还没有可统计的用量。每次对话结束后,token 计数会连同「当时是哪个模型
              在服务」一起写进消息记录,之后在这里按厂商汇总。
            </Note>
          )}

          {data!.groups.map((g) => (
            <SectionCard key={g.name} title={g.name}>
              {g.models.map((m) => (
                <UsageModelRow key={`${m.modelId}#${m.attribution}`} model={m} />
              ))}
            </SectionCard>
          ))}

          {hasData && (
            <Note kind="info">
              总计包含所有已计费的消息,包括会话记录已被删除的孤立行 —— 那些 token
              确实产生了消耗,丢掉会让总额偏低且无从察觉。
            </Note>
          )}
        </>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 记忆 (Memory) — 与 MemoryTools 同一目录,支持编辑 GLOBAL.md / 每日日志
// ---------------------------------------------------------------------------
export function MemoryPage(props: { onBack: () => void }) {
  const [list, setList] = useState<MemoryFileInfo[] | null>(null)
  const [dir, setDir] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [name, setName] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [newName, setNewName] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.memoryList()
      setList(r.files)
      setDir(r.dir)
      if (name && !r.files.some((f) => f.name === name)) setName(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [name])

  useEffect(() => {
    void load()
  }, [load])

  const openFile = async (n: string) => {
    setError(null)
    setName(n)
    setContent('')
    try {
      const doc = await api.memoryGet(n)
      setContent(doc.content)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const saveFile = async () => {
    if (!name) return
    setSaving(true)
    setError(null)
    setSaved(null)
    try {
      await api.memoryPut(name, content)
      setSaved(`已保存 ${name} ✓`)
      setTimeout(() => setSaved(null), 2000)
      void load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const removeFile = async (n: string) => {
    if (!window.confirm(`删除记忆文件「${n}」?此操作不可撤销。`)) return
    setError(null)
    try {
      await api.memoryDelete(n)
      if (name === n) {
        setName(null)
        setContent('')
      }
      void load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const createFile = async () => {
    const n = newName.trim()
    if (!n) return
    const safe = n.endsWith('.md') || n.endsWith('.txt') ? n : `${n}.md`
    setError(null)
    try {
      await api.memoryPut(safe, '# ' + safe.replace(/\.(md|txt)$/, '') + '\n\n')
      setNewName('')
      await openFile(safe)
      void load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (loading) return <DetailShell title="记忆 (Memory)" onBack={props.onBack}><Spinner /></DetailShell>
  const files = list ?? []

  return (
    <DetailShell
      title="记忆 (Memory)"
      subtitle="长期记忆目录(memory/):GLOBAL.md 由你在本页人工维护,YYYY-MM-DD.md 每日日志由 Agent 自动写入。"
      onBack={props.onBack}
    >
      <StatusLine ok={saved} err={error} />
      <p className="muted pathline">目录: {dir}</p>

      <SectionCard title="文件" hint={`${files.length} 个文件,点击加载内容进行编辑`}>
        {files.length === 0 && <p className="empty">还没有记忆文件。Agent 每次 memory_write 都会创建当天的日志。</p>}
        <div className="memory-file-list">
          {files.map((f) => (
            <div key={f.name} className={`memory-file ${name === f.name ? 'active' : ''}`}>
              <button className="memory-file-main" onClick={() => void openFile(f.name)}>
                <span className="mf-name">{f.name}</span>
                <span className="mf-preview muted">{f.preview}</span>
                <span className="mf-meta muted">{f.size} B</span>
              </button>
              <button className="link danger" onClick={() => void removeFile(f.name)}>删除</button>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="新建记忆文件"
        hint="适合人工维护的长期主题(如 GLOBAL.md 之外的约定/清单)。名称会自动补 .md。"
      >
        <div className="row-actions">
          <input value={newName} placeholder="如 project-notes"
            onChange={(e) => setNewName(e.target.value)} />
          <button onClick={() => void createFile()}>新建</button>
        </div>
      </SectionCard>

      {name && (
        <SectionCard title={`编辑 · ${name}`}>
          <textarea
            className="mono memory-editor"
            rows={14}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
          />
          <div className="row-actions">
            <span className="muted">覆盖保存整个文件内容。</span>
            <button onClick={() => void saveFile()} disabled={saving}>
              {saving ? 'Saving…' : '保存'}
            </button>
          </div>
        </SectionCard>
      )}
    </DetailShell>
  )
}
