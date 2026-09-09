import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { ChatSessionInfo, WorkspaceInfo } from '../types'

export type ViewId = 'chat' | 'workspaces' | 'sandbox' | 'settings' | 'projects' | 'knowledge' | 'memory' | 'skills' | 'market' | 'channels' | 'scheduler'

interface NavItem {
  id: ViewId
  label: string
  icon: string
}

const TOP_NAV: NavItem[] = [
  { id: 'chat', label: '聊天', icon: '💬' },
  { id: 'workspaces', label: '助理', icon: '🐾' },
  { id: 'knowledge', label: '知识', icon: '📚' },
  { id: 'memory', label: '记忆', icon: '🧠' },
  { id: 'skills', label: '技能', icon: '⚡' },
  { id: 'market', label: '广场', icon: '🛍️' },
  { id: 'channels', label: '通道', icon: '📡' },
  { id: 'scheduler', label: '定时', icon: '⏰' },
  { id: 'projects', label: '项目', icon: '◇' },
  { id: 'sandbox', label: '沙箱', icon: '⏚' },
]

interface SidebarProps {
  view: ViewId
  activeSessionId: string | null
  onChangeView: (v: ViewId) => void
  onSelectSession: (id: string) => void
  onCreateSession: (folderId?: string | null) => Promise<void>
  onDeleteSession: (id: string) => void
  onCreateWorkspace: (name: string, path?: string) => Promise<void>
  onDeleteWorkspace: (id: string) => void
}

/**
 * The single navigation column on the left.
 *
 * Inspired by WorkBuddy's own rail: top-level actions, then a grouped
 * "空间 (N)" section listing workspaces (each expandable to its sessions),
 * then a footer with 设置 and a user/badge chip.
 */
export function Sidebar(props: SidebarProps) {
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [sessions, setSessions] = useState<ChatSessionInfo[]>([])
  const [wsOpen, setWsOpen] = useState<Set<string>>(new Set()) // workspace ids expanded
  const [allOpen, setAllOpen] = useState(true)
  const [unfiledOpen, setUnfiledOpen] = useState(true)
  const [showNewWs, setShowNewWs] = useState(false)
  const [newName, setNewName] = useState('')
  const [newPath, setNewPath] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reloadWorkspaces = useCallback(async () => {
    try {
      const r = await api.workspaces()
      setWorkspaces(r.workspaces)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  const reloadSessions = useCallback(async () => {
    try {
      const r = await api.chatSessions()
      setSessions(r.sessions)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  useEffect(() => {
    void reloadWorkspaces()
    void reloadSessions()
  }, [reloadSessions, reloadWorkspaces])

  const sessionsByWs = useMemo(() => {
    const map = new Map<string | null, ChatSessionInfo[]>()
    for (const s of sessions) {
      const k = s.folderId ?? null
      if (!map.has(k)) map.set(k, [])
      map.get(k)!.push(s)
    }
    return map
  }, [sessions])

  const toggleWs = (id: string) => {
    setWsOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleCreateWs = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      await props.onCreateWorkspace(name, newPath.trim() || undefined)
      setNewName('')
      setNewPath('')
      setShowNewWs(false)
      await reloadWorkspaces()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const handleNewSession = async (folderId?: string | null) => {
    try {
      await props.onCreateSession(folderId ?? null)
      await reloadSessions()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const handleDeleteSess = async (id: string) => {
    if (!confirm('删除这个会话?消息记录也会一并删除。')) return
    props.onDeleteSession(id)
    await reloadSessions()
  }

  const handleDeleteWs = async (id: string) => {
    if (!confirm('删除这个工作空间?其会话将变为未分组。')) return
    props.onDeleteWorkspace(id)
    await Promise.all([reloadWorkspaces(), reloadSessions()])
  }

  const totalCount = sessions.length

  return (
    <aside className="nav-rail">
      {/* top brand + main actions */}
      <div className="nav-brand">
        <span className="brand-name">
          Open<span className="brand-accent">Minis</span>
        </span>
      </div>

      <button
        className="nav-pri"
        onClick={() => void handleNewSession(null)}
        title="新建一个未分组的会话并切到聊天"
      >
        <span className="ic">＋</span>
        <span>新建任务</span>
      </button>

      <nav className="nav-list">
        {TOP_NAV.map((n) => {
          if (n.id === 'chat') {
            return (
              <button
                key={n.id}
                className={`nav-item ${props.view === 'chat' ? 'active' : ''}`}
                onClick={() => props.onChangeView('chat')}
                title="跳到最近一个会话"
              >
                <span className="ic">{n.icon === '＋' ? '＋' : n.icon}</span>
                <span>{n.label}</span>
              </button>
            )
          }
          return (
            <button
              key={n.id}
              className={`nav-item ${props.view === n.id ? 'active' : ''}`}
              onClick={() => props.onChangeView(n.id)}
            >
              <span className="ic">{n.icon}</span>
              <span>{n.label}</span>
            </button>
          )
        })}
      </nav>

      {/* space group header */}
      <div className="nav-section">
        <header className="nav-section-head">
          <button
            className="nav-section-title"
            onClick={() => setAllOpen((v) => !v)}
            title="全部会话"
          >
            <span className="caret">{allOpen ? '⌄' : '›'}</span>
            <span className="ic">⌂</span>
            <span className="lbl">空间</span>
            <span className="badge">{totalCount}</span>
          </button>
          <button
            className="link"
            onClick={() => setShowNewWs((v) => !v)}
            title="新建工作空间"
          >
            ＋
          </button>
        </header>

        {showNewWs && (
          <form
            className="ws-new-card"
            onSubmit={(e) => {
              e.preventDefault()
              void handleCreateWs()
            }}
          >
            <input
              autoFocus
              placeholder="工作空间名…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <input
              placeholder="电脑目录(可选,例 D:\proj)…"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
            />
            <div className="row">
              <button
                type="button"
                className="link"
                onClick={() => setShowNewWs(false)}
              >
                取消
              </button>
              <button type="submit">创建</button>
            </div>
          </form>
        )}

        {allOpen && (
          <ul className="ws-tree">
            {/* 全部: 平铺所有会话 */}
            <li className="ws-row all">
              <button
                className={`ws-row-head ${!props.activeSessionId ? 'active' : ''}`}
                onClick={() => props.onChangeView('chat')}
              >
                <span className="caret">⌄</span>
                <span className="ic ic-folder">📁</span>
                <span className="lbl">全部</span>
                <span className="badge">{totalCount}</span>
              </button>
              <ul className="sess-tree">
                {sessions.length === 0 && (
                  <li className="sess-empty">暂无会话</li>
                )}
                {sessions.map((s) => (
                  <li
                    key={s.id}
                    className={`sess-item ${
                      props.activeSessionId === s.id ? 'active' : ''
                    }`}
                  >
                    <button
                      className="sess-row"
                      onClick={() => {
                        props.onSelectSession(s.id)
                        props.onChangeView('chat')
                      }}
                      title={s.title}
                    >
                      <span className="ic">💬</span>
                      <span className="lbl">{s.title}</span>
                    </button>
                    <button
                      className="sess-x"
                      title="删除"
                      onClick={() => void handleDeleteSess(s.id)}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            </li>

            {/* 未分组 */}
            {(() => {
              const us = sessionsByWs.get(null) ?? []
              if (us.length === 0) return null
              return (
                <li className="ws-row">
                  <button
                    className="ws-row-head"
                    onClick={() => setUnfiledOpen((v) => !v)}
                  >
                    <span className="caret">
                      {unfiledOpen ? '⌄' : '›'}
                    </span>
                    <span className="ic ic-folder">∅</span>
                    <span className="lbl">未分组</span>
                    <span className="badge">{us.length}</span>
                  </button>
                  {unfiledOpen && (
                    <ul className="sess-tree">
                      {us.map((s) => (
                        <li
                          key={s.id}
                          className={`sess-item ${
                            props.activeSessionId === s.id ? 'active' : ''
                          }`}
                        >
                          <button
                            className="sess-row"
                            onClick={() => {
                              props.onSelectSession(s.id)
                              props.onChangeView('chat')
                            }}
                            title={s.title}
                          >
                            <span className="ic">💬</span>
                            <span className="lbl">{s.title}</span>
                          </button>
                          <button
                            className="sess-x"
                            title="删除"
                            onClick={() => void handleDeleteSess(s.id)}
                          >
                            ×
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })()}

            {/* 工作空间分组 */}
            {workspaces.map((w) => {
              const ws = sessionsByWs.get(w.id) ?? []
              const open = wsOpen.has(w.id)
              return (
                <li key={w.id} className="ws-row">
                  <button
                    className={`ws-row-head ${
                      sessionsByWs.get(w.id)?.some(
                        (s) => s.id === props.activeSessionId,
                      )
                        ? 'active'
                        : ''
                    }`}
                    onClick={() => toggleWs(w.id)}
                  >
                    <span className="caret">{open ? '⌄' : '›'}</span>
                    <span className="ic ic-folder">📁</span>
                    <span className="lbl">{w.name}</span>
                    <span className="badge">{ws.length}</span>
                    <button
                      className="ws-x"
                      title="删除工作空间"
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleDeleteWs(w.id)
                      }}
                    >
                      ×
                    </button>
                  </button>
                  {open && (
                    <ul className="sess-tree">
                      {ws.length === 0 && (
                        <li className="sess-empty">
                          <button
                            className="link"
                            onClick={() => void handleNewSession(w.id)}
                          >
                            ＋ 在此新建会话
                          </button>
                        </li>
                      )}
                      {ws.map((s) => (
                        <li
                          key={s.id}
                          className={`sess-item ${
                            props.activeSessionId === s.id ? 'active' : ''
                          }`}
                        >
                          <button
                            className="sess-row"
                            onClick={() => {
                              props.onSelectSession(s.id)
                              props.onChangeView('chat')
                            }}
                            title={s.title}
                          >
                            <span className="ic">💬</span>
                            <span className="lbl">{s.title}</span>
                          </button>
                          <button
                            className="sess-x"
                            title="删除"
                            onClick={() => void handleDeleteSess(s.id)}
                          >
                            ×
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {error && <div className="nav-error">{error}</div>}

      <footer className="nav-foot">
        <button
          className="nav-foot-item"
          onClick={() => props.onChangeView('settings')}
          title="设置"
        >
          <span className="ic">⚙</span>
          <span>设置</span>
        </button>
        <span className="nav-user" title="当前账号">
          <span className="user-chip">👤</span>
          <span className="user-name">user</span>
        </span>
      </footer>
    </aside>
  )
}
