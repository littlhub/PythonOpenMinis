import { useEffect, useState } from 'react'
import { api } from '../api'
import type {
  ExternalSkillInfo,
  SkillDetail,
  SkillEnvInfo,
  SkillInfo,
  SkillInstallTarget,
  SkillToolInfo,
} from '../types'

type Filter = 'all' | 'active' | 'builtin' | 'user' | 'external' | 'tools'

/**
 * SkillsView - 技能管理页面
 *
 * 展示 <data_dir>/skills 下真实安装的技能包（每个含 SKILL.md）以及内核当前
 * 暴露的内置工具清单；支持从本地目录 / zip 安装与卸载。
 *
 * **安装是可指定落点的**：主智能体技能库（`host`）或任一外部 agent 自己的
 * 技能根（`<agentId>#<序号>`，由它的协议描述符 `skills.roots` 声明）。所以
 * 「每个 agent 都能加技能」在这一页完成 —— 装到外部 agent 的根里不进主智能体
 * 的 activeSkills，那是它自己循环里用的技能。
 *
 * 「外部 agent 技能」另开一栏：它们的正文不在主智能体技能库里，只有那个 agent
 * 会加载；这里可以查看、卸载、以及继续往同一个根里装。
 */
export function SkillsView() {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [tools, setTools] = useState<SkillToolInfo[]>([])
  const [external, setExternal] = useState<ExternalSkillInfo[]>([])
  const [targets, setTargets] = useState<SkillInstallTarget[]>([])
  const [target, setTarget] = useState('host')
  const [dir, setDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [openName, setOpenName] = useState<string | null>(null)
  const [detail, setDetail] = useState<SkillDetail | null>(null)
  const [installPath, setInstallPath] = useState('')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [envInfo, setEnvInfo] = useState<SkillEnvInfo | null>(null)
  const [envDraft, setEnvDraft] = useState<Record<string, string>>({})
  const [showEnv, setShowEnv] = useState(false)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.skillsList()
      setSkills(data.skills)
      setTools(data.tools)
      setExternal(data.external ?? [])
      const nextTargets = data.targets ?? []
      setTargets(nextTargets)
      // 落点可能因为描述符改动而消失（比如那个槽位被停用），别留一个悬空选择。
      setTarget((cur) =>
        nextTargets.some((t) => t.id === cur) ? cur : (nextTargets[0]?.id ?? 'host'),
      )
      setDir(data.dir)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
    // 环境变量是另一条口子 —— 读不到不该把整页拖成「加载失败」。
    try {
      const env = await api.skillsEnv()
      setEnvInfo(env)
      setEnvDraft(env.values)
    } catch {
      setEnvInfo(null)
    }
  }

  const missingEnv = (skill: SkillInfo) =>
    (skill.env ?? []).filter((name) => !(envInfo?.values?.[name] || '').trim())

  const saveEnv = async () => {
    setBusy(true)
    setNotice('')
    try {
      const res = await api.skillsEnvSave(envDraft)
      setEnvInfo({ required: res.required, values: res.values, count: res.count })
      setEnvDraft(res.values)
      setNotice(`已保存 ${res.count} 个环境变量（新命令立刻生效）`)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  /** 展开某份技能的正文。key 带落点前缀，避免外部与主智能体同名时串台。 */
  const openSkill = async (name: string, target = 'host') => {
    const key = target === 'host' ? name : `${target}:${name}`
    if (openName === key) {
      setOpenName(null)
      setDetail(null)
      return
    }
    setOpenName(key)
    setDetail(null)
    setNotice('')
    try {
      setDetail(await api.skillGet(name, target))
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    }
  }

  const doInstall = async () => {
    const path = installPath.trim()
    if (!path) return
    setBusy(true)
    setNotice('')
    try {
      const { skill, path: landed } = await api.skillInstall(path, force, target)
      const at = targets.find((t) => t.id === target)
      setNotice(
        at && at.kind === 'external'
          ? `已把技能 ${skill.name} 装进「${at.agentName}」的技能根：${landed ?? at.path}`
          : `已安装技能 ${skill.name}`,
      )
      setInstallPath('')
      setForce(false)
      await load()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const doToggle = async (skill: SkillInfo) => {
    setBusy(true)
    setNotice('')
    try {
      if (skill.active) {
        await api.skillDeactivate(skill.name)
        setNotice(`已停用 ${skill.name}（主 agent 不再调用它）`)
      } else {
        await api.skillActivate(skill.name)
        setNotice(`已激活 ${skill.name}，主 agent 现在可以通过 skill_use 调用它`)
      }
      await load()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async (name: string, target = 'host') => {
    const at = targets.find((t) => t.id === target)
    const where = at && at.kind === 'external' ? `（${at.agentName} 的技能根）` : ''
    if (!window.confirm(`卸载技能「${name}」${where}？这会删除它的目录。`)) return
    setBusy(true)
    setNotice('')
    try {
      await api.skillDelete(name, target)
      setNotice(`已卸载 ${name}`)
      const key = target === 'host' ? name : `${target}:${name}`
      if (openName === key) {
        setOpenName(null)
        setDetail(null)
      }
      await load()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const match = (s: SkillInfo) =>
    !search ||
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    s.description.toLowerCase().includes(search.toLowerCase())

  const filtered = skills.filter((s) => {
    if (filter === 'active') return s.active && match(s)
    if (filter === 'builtin') return s.source === 'builtin' && match(s)
    if (filter === 'user') return s.source !== 'builtin' && match(s)
    // 「外部 agent」那一栏是另一份数据（不在 skills 里），这里不该再列出技能。
    if (filter === 'external') return false
    return match(s)
  })

  const filteredTools = tools.filter(
    (t) =>
      !search ||
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.description.toLowerCase().includes(search.toLowerCase()),
  )

  const externalMatch = (s: ExternalSkillInfo) =>
    !search ||
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    (s.description ?? '').toLowerCase().includes(search.toLowerCase()) ||
    (s.agentName ?? '').toLowerCase().includes(search.toLowerCase())

  const filteredExternal = external.filter(externalMatch)

  const installTarget = targets.find((t) => t.id === target)

  const categories: { id: Filter; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: 'active', label: `已激活 ${skills.filter((s) => s.active).length}` },
    { id: 'builtin', label: '内置' },
    { id: 'user', label: '自定义' },
    ...(external.length > 0
      ? [{ id: 'external' as Filter, label: `外部 agent ${external.length}` }]
      : []),
    { id: 'tools', label: '内置工具' },
  ]

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>⚡ 技能管理</h2>
          <span className="kb-count">
            {loading
              ? '加载中…'
              : `${skills.length} 个技能 · ${tools.length} 个内置工具${
                  external.length > 0 ? ` · ${external.length} 个外部 agent 技能` : ''
                }`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}
        {notice && <div className="note note-ok">{notice}</div>}

        <div className="kb-search">
          <span className="kb-search-icon">🔍</span>
          <input
            type="text"
            placeholder="搜索技能或工具..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="kb-categories">
          {categories.map((cat) => (
            <button
              key={cat.id}
              className={`kb-cat-btn ${filter === cat.id ? 'active' : ''}`}
              onClick={() => setFilter(cat.id)}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {dir && <div className="pathline muted">技能目录：{dir}</div>}

        {/* 环境变量：技能在 SKILL.md 里声明「我需要什么」，这里填值。
            存的就是设置 → 系统 →「环境变量」那一份，沙箱命令立刻可见。 */}
        {envInfo && envInfo.required.length > 0 && (
          <div className="set-section" style={{ marginTop: 10 }}>
            <div className="set-title">
              环境变量
              {envInfo.required.some((r) => !r.set) && (
                <span className="kb-count">
                  {' '}
                  还差 {envInfo.required.filter((r) => !r.set).length} 项
                </span>
              )}
              <button
                className="memory-action-btn"
                style={{ marginLeft: 8 }}
                onClick={() => setShowEnv((v) => !v)}
              >
                {showEnv ? '收起' : '填写'}
              </button>
            </div>
            {showEnv && (
              <div className="plugin-form" style={{ marginTop: 8 }}>
                {envInfo.required.map((r) => (
                  <label className="plugin-field" key={r.name}>
                    <span className="plugin-field-label">
                      {r.name}
                      {!r.set && <i className="req">*</i>}
                      <span className="muted"> · {r.skills.join('、')} 用</span>
                    </span>
                    <input
                      type="password"
                      value={envDraft[r.name] ?? ''}
                      placeholder={r.set ? '已填' : '还没填'}
                      onChange={(e) =>
                        setEnvDraft({ ...envDraft, [r.name]: e.target.value })
                      }
                    />
                  </label>
                ))}
                <div className="plugin-form-foot">
                  <button
                    className="plugin-btn primary"
                    disabled={busy}
                    onClick={() => void saveEnv()}
                  >
                    {busy ? '保存中…' : '保存'}
                  </button>
                  <span className="muted" style={{ fontSize: 11 }}>
                    技能脚本读 os.getenv / $VAR 取的就是这里；保存后新命令立刻生效
                  </span>
                </div>
              </div>
            )}
          </div>
        )}

        {filter === 'tools' ? (
          <div className="kb-list">
            {filteredTools.map((t) => (
              <div key={t.name} className="kb-item">
                <div className="kb-item-header">
                  <span className="kb-item-title mono">{t.name}</span>
                </div>
                <div className="kb-item-summary">{t.description}</div>
                {Object.keys(t.parameters).length > 0 && (
                  <div className="kb-item-footer muted">
                    参数：{Object.keys(t.parameters).join('、')}
                  </div>
                )}
              </div>
            ))}
            {filteredTools.length === 0 && <div className="kb-empty">没有匹配的工具</div>}
          </div>
        ) : filter === 'external' ? (
          /* 外部 agent 自己的技能：主智能体不加载它们（要动就动那个 agent 的技能根），
             所以这里没有「激活」；但可以查看正文、卸载、以及把「安装到」切到它再装。 */
          <div className="kb-list">
            {filteredExternal.map((s) => {
              const key = `${s.targetId || s.agentId}:${s.name}`
              const open = openName === key
              return (
                <div key={`${s.agentId}:${s.path}`} className="kb-item">
                  <div className="kb-item-header">
                    <span className="kb-item-title">⚡ {s.name}</span>
                    <span className="kb-count">
                      {s.agentName}（{s.agentId}） · {s.scope}/{s.source}
                    </span>
                  </div>
                  <div className="kb-item-summary">
                    {s.whenToUse || s.description || '（无描述）'}
                  </div>
                  {s.shadowed && (
                    <div className="kb-item-footer muted">
                      与主智能体内的同名技能并存 —— 两边是各自的正文，不做合并。
                    </div>
                  )}
                  <div className="kb-item-footer muted mono">{s.path}</div>
                  <div className="kb-item-action">
                    <button
                      className="memory-action-btn"
                      onClick={() => void openSkill(s.name, s.targetId || 'host')}
                    >
                      {open ? '收起' : '查看'}
                    </button>
                    <button
                      className="memory-action-btn"
                      disabled={busy || !s.targetId}
                      title={s.targetId ? '卸载技能' : '这个技能不属于任何已知落点，无法删除'}
                      onClick={() => void doDelete(s.name, s.targetId || 'host')}
                    >
                      卸载
                    </button>
                  </div>
                  {open && detail && (
                    <div style={{ marginTop: 8 }}>
                      <div className="kb-item-footer muted">
                        只读 —— 正文属于 {s.agentName} 自己的技能根
                      </div>
                      <div className="memory-content">{detail.content}</div>
                    </div>
                  )}
                </div>
              )
            })}
            {filteredExternal.length === 0 && (
              <div className="kb-empty">
                没有匹配的外部 agent 技能。外部 agent 的技能根在它的协议描述符里声明
                （skills.roots）；槽位未启用时这一栏也会是空的。想在它那儿放技能，把下面
                的「安装到」切到它再装即可。
              </div>
            )}
          </div>
        ) : (
          <div className="skills-grid">
            {filtered.map((skill) => (
              <div
                key={skill.name}
                className={`skill-card ${skill.active ? 'enabled' : 'inactive'}`}
              >
                <div className="skill-icon">
                  {skill.generated ? '🧰' : skill.source === 'builtin' ? '⚙️' : '📦'}
                </div>
                <div className="skill-info">
                  <div className="skill-name">
                    {skill.name}
                    {skill.generated && <span className="kb-count"> 自动生成</span>}
                  </div>
                  <div className="skill-desc">
                    {skill.description || '（无描述）'}
                  </div>
                  {missingEnv(skill).length > 0 && (
                    <div className="muted" style={{ fontSize: 11, color: 'var(--danger)' }}>
                      缺环境变量：{missingEnv(skill).join('、')}
                    </div>
                  )}
                  <div className="kb-item-footer muted">
                    {skill.source === 'builtin' ? '内置' : '自定义'}
                    {skill.scripts.length > 0 && ` · 脚本 ${skill.scripts.length}`}
                    {!skill.active && ' · 未激活'}
                  </div>
                </div>
                <div className="kb-item-action">
                  <label
                    className="skill-toggle"
                    title={
                      skill.active
                        ? '已激活：点击停用（主 agent 不再调用）'
                        : '已停用：点击激活（主 agent 可通过 skill_use 调用）'
                    }
                  >
                    <input
                      type="checkbox"
                      checked={skill.active}
                      disabled={busy}
                      onChange={() => void doToggle(skill)}
                    />
                    <span className="skill-toggle-track" />
                  </label>
                  <button
                    className="memory-action-btn"
                    onClick={() => void openSkill(skill.name)}
                  >
                    {openName === skill.name ? '收起' : '查看'}
                  </button>
                  <button
                    className="memory-action-btn"
                    disabled={skill.generated || busy}
                    title={skill.generated ? '自动生成的技能不可卸载' : '卸载技能'}
                    onClick={() => void doDelete(skill.name)}
                  >
                    卸载
                  </button>
                </div>
              </div>
            ))}
            {filtered.length === 0 && !loading && (
              <div className="kb-empty">没有找到匹配的技能</div>
            )}
          </div>
        )}

        {detail && openName && (
          <div style={{ marginTop: 12 }}>
            <div className="kb-item-header">
              <span className="kb-item-title">{detail.name}</span>
              <span className="kb-item-date mono">{detail.path}</span>
            </div>
            <div className="memory-content">{detail.content}</div>
          </div>
        )}

        <div className="form-group" style={{ marginTop: 12 }}>
          {targets.length > 1 && (
            <label className="cfg-row" style={{ marginBottom: 6 }}>
              <span className="cfg-label" style={{ minWidth: 64 }}>
                安装到
              </span>
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                style={{ flex: 1 }}
              >
                {targets.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.kind === 'host'
                      ? `主智能体技能库 — ${t.path}`
                      : `${t.agentName}（${t.id}）— ${t.path}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="field-row">
            <input
              type="text"
              placeholder="本地技能目录或 .zip 包路径"
              value={installPath}
              onChange={(e) => setInstallPath(e.target.value)}
            />
            <button
              className="btn-create"
              disabled={busy || !installPath.trim()}
              onClick={() => void doInstall()}
            >
              {busy ? '安装中…' : '安装技能'}
            </button>
          </div>
          <label className="cfg-row" style={{ marginTop: 6 }}>
            <input
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
            />
            <span className="cfg-label">同名技能已存在时覆盖</span>
          </label>
          {installTarget && installTarget.kind === 'external' && (
            <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
              装进「{installTarget.agentName}」自己的技能根 {installTarget.path} ——
              主智能体不加载这份技能，只有它会用。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
