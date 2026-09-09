import { useEffect, useState } from 'react'
import { api } from '../api'
import type { SkillDetail, SkillInfo, SkillToolInfo } from '../types'

type Filter = 'all' | 'active' | 'builtin' | 'user' | 'tools'

/**
 * SkillsView - 技能管理页面
 *
 * 展示 <data_dir>/skills 下真实安装的技能包（每个含 SKILL.md）以及内核当前
 * 暴露的内置工具清单；支持从本地目录 / zip 安装与卸载。
 */
export function SkillsView() {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [tools, setTools] = useState<SkillToolInfo[]>([])
  const [dir, setDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [openName, setOpenName] = useState<string | null>(null)
  const [detail, setDetail] = useState<SkillDetail | null>(null)
  const [installPath, setInstallPath] = useState('')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.skillsList()
      setSkills(data.skills)
      setTools(data.tools)
      setDir(data.dir)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const openSkill = async (name: string) => {
    if (openName === name) {
      setOpenName(null)
      setDetail(null)
      return
    }
    setOpenName(name)
    setDetail(null)
    setNotice('')
    try {
      setDetail(await api.skillGet(name))
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
      const { skill } = await api.skillInstall(path, force)
      setNotice(`已安装技能 ${skill.name}`)
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

  const doDelete = async (name: string) => {
    if (!window.confirm(`卸载技能「${name}」？这会删除它的目录。`)) return
    setBusy(true)
    setNotice('')
    try {
      await api.skillDelete(name)
      setNotice(`已卸载 ${name}`)
      if (openName === name) {
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
    return match(s)
  })

  const filteredTools = tools.filter(
    (t) =>
      !search ||
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.description.toLowerCase().includes(search.toLowerCase()),
  )

  const categories: { id: Filter; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: 'active', label: `已激活 ${skills.filter((s) => s.active).length}` },
    { id: 'builtin', label: '内置' },
    { id: 'user', label: '自定义' },
    { id: 'tools', label: '内置工具' },
  ]

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>⚡ 技能管理</h2>
          <span className="kb-count">
            {loading ? '加载中…' : `${skills.length} 个技能 · ${tools.length} 个内置工具`}
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
        </div>
      </div>
    </div>
  )
}
