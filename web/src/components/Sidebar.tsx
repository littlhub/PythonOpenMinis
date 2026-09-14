import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { ChatSessionInfo, ConsoleStatus, WorkspaceInfo } from '../types'

export type ViewId = 'chat' | 'workspaces' | 'sandbox' | 'settings' | 'projects' | 'knowledge' | 'memory' | 'skills' | 'market' | 'channels' | 'scheduler'

interface NavItem {
  id: ViewId
  label: string
  icon: string
}

const TOP_NAV: NavItem[] = [{ id: 'chat', label: '聊天', icon: '💬' }]

//: 「管理」折叠分组 —— 助理/知识/记忆/技能/广场/通道/定时 七个一起收起来，
//: 点分组标题展开（默认收起；当前页在组里时自动展开）。
const MANAGE_NAV: NavItem[] = [
  { id: 'workspaces', label: '助理', icon: '🐾' },
  { id: 'knowledge', label: '知识', icon: '📚' },
  { id: 'memory', label: '记忆', icon: '🧠' },
  { id: 'skills', label: '技能', icon: '⚡' },
  { id: 'market', label: '广场', icon: '🛍️' },
  { id: 'channels', label: '通道', icon: '📡' },
  { id: 'scheduler', label: '定时', icon: '⏰' },
]

const TAIL_NAV: NavItem[] = [
  { id: 'projects', label: '项目', icon: '◇' },
  { id: 'sandbox', label: '沙箱', icon: '⏚' },
]

const MANAGE_STORAGE_KEY = 'openminis:nav-manage'

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
  const [flatOpen, setFlatOpen] = useState(true)
  const [unfiledOpen, setUnfiledOpen] = useState(true)
  const [showNewWs, setShowNewWs] = useState(false)
  const [newName, setNewName] = useState('')
  const [newPath, setNewPath] = useState('')
  const [error, setError] = useState<string | null>(null)
  // 「管理」分组默认收起；用户点开后记住 (localStorage)。
  const [manageOpen, setManageOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(MANAGE_STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  // -- 用户按钮：进入页面的访问密码 -------------------------------------
  const [userOpen, setUserOpen] = useState(false)
  const [accessSt, setAccessSt] = useState<ConsoleStatus | null>(null)
  const [accessPwd, setAccessPwd] = useState('') // 解锁 / 首次设置
  const [accessCur, setAccessCur] = useState('') // 修改密码时的当前密码
  const [accessNew, setAccessNew] = useState('')
  const [accessMsg, setAccessMsg] = useState<string | null>(null)

  const loadAccessStatus = useCallback(async () => {
    try {
      setAccessSt(await api.accessStatus())
    } catch {
      setAccessSt(null)
      setAccessMsg('读取访问密码状态失败')
    }
  }, [])

  const openUserPanel = () => {
    setUserOpen((v) => !v)
    setAccessMsg(null)
    setAccessPwd('')
    setAccessCur('')
    setAccessNew('')
    void loadAccessStatus()
  }

  const accessUnlock = async () => {
    try {
      await api.accessUnlock(accessPwd)
      setAccessMsg('已解锁')
      setAccessPwd('')
      await loadAccessStatus()
    } catch {
      setAccessMsg('密码不正确')
    }
  }

  const accessSet = async () => {
    const target = accessSt?.hasPassword ? accessNew : accessPwd
    if (!target.trim()) {
      setAccessMsg('密码不能为空')
      return
    }
    try {
      await api.accessSetPassword(target, accessSt?.hasPassword ? accessCur : '')
      setAccessMsg(accessSt?.hasPassword ? '密码已修改' : '密码已设置')
      setAccessPwd('')
      setAccessCur('')
      setAccessNew('')
      await loadAccessStatus()
    } catch {
      setAccessMsg('设置失败：当前密码不正确？')
    }
  }

  const accessClear = async () => {
    try {
      await api.accessSetPassword('', accessCur)
      setAccessMsg('密码已清除')
      setAccessPwd('')
      setAccessCur('')
      setAccessNew('')
      await loadAccessStatus()
    } catch {
      setAccessMsg('清除失败：当前密码不正确？')
    }
  }

  // 当前页正好在分组里时自动展开，避免"看不到自己在哪"。
  const manageActive = MANAGE_NAV.some((n) => n.id === props.view)
  const manageExpanded = manageOpen || manageActive

  const toggleManage = () => {
    setManageOpen((v) => {
      const next = !v
      try {
        localStorage.setItem(MANAGE_STORAGE_KEY, next ? '1' : '0')
      } catch {
        /* 隐私模式下 localStorage 可能不可用，忽略 */
      }
      return next
    })
  }

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

        {/* 「管理」折叠分组：记忆 / 技能 / 广场 / 通道 / 定时 */}
        <div className={`nav-group ${manageExpanded ? 'open' : ''}`}>
          <button
            className={`nav-item nav-group-head ${manageActive ? 'active' : ''}`}
            onClick={toggleManage}
            title={manageExpanded ? '收起管理菜单' : '展开管理菜单'}
          >
            <span className="caret">{manageExpanded ? '⌄' : '›'}</span>
            <span className="ic">🗂</span>
            <span className="lbl">管理</span>
            <span className="badge">{MANAGE_NAV.length}</span>
          </button>
          {manageExpanded && (
            <div className="nav-sublist">
              {MANAGE_NAV.map((n) => (
                <button
                  key={n.id}
                  className={`nav-item sub ${props.view === n.id ? 'active' : ''}`}
                  onClick={() => props.onChangeView(n.id)}
                >
                  <span className="ic">{n.icon}</span>
                  <span>{n.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {TAIL_NAV.map((n) => (
          <button
            key={n.id}
            className={`nav-item ${props.view === n.id ? 'active' : ''}`}
            onClick={() => props.onChangeView(n.id)}
          >
            <span className="ic">{n.icon}</span>
            <span>{n.label}</span>
          </button>
        ))}
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
            {/* 全部: 平铺所有会话（点标题行折叠/展开） */}
            <li className="ws-row all">
              <button
                className={`ws-row-head ${!props.activeSessionId ? 'active' : ''}`}
                onClick={() => setFlatOpen((v) => !v)}
                title={flatOpen ? '收起全部会话' : '展开全部会话'}
              >
                <span className="caret">{flatOpen ? '⌄' : '›'}</span>
                <span className="ic ic-folder">📁</span>
                <span className="lbl">全部</span>
                <span className="badge">{totalCount}</span>
              </button>
              {flatOpen && (
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
              )}
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
        {userOpen && (
          <div className="user-panel">
            <div className="up-head">🔐 进入密码</div>
            {!accessSt ? (
              <div className="up-msg">{accessMsg ?? '读取中…'}</div>
            ) : (
              <>
                <div className="up-status">
                  {!accessSt.hasPassword
                    ? '未设置密码，页面无需密码即可进入'
                    : accessSt.locked
                      ? '🔒 已设密码 · 页面已锁定'
                      : '🔓 已解锁'}
                </div>
                {!accessSt.hasPassword && (
                  <div className="up-row">
                    <input
                      type="password"
                      placeholder="设置进入密码"
                      value={accessPwd}
                      onChange={(e) => setAccessPwd(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void accessSet()
                      }}
                    />
                    <button onClick={() => void accessSet()}>设置</button>
                  </div>
                )}
                {accessSt.hasPassword && accessSt.locked && (
                  <div className="up-row">
                    <input
                      type="password"
                      placeholder="输入密码解锁"
                      value={accessPwd}
                      onChange={(e) => setAccessPwd(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void accessUnlock()
                      }}
                    />
                    <button onClick={() => void accessUnlock()}>解锁</button>
                  </div>
                )}
                {accessSt.hasPassword && !accessSt.locked && (
                  <>
                    <div className="up-row">
                      <input
                        type="password"
                        placeholder="当前密码"
                        value={accessCur}
                        onChange={(e) => setAccessCur(e.target.value)}
                      />
                      <input
                        type="password"
                        placeholder="新密码"
                        value={accessNew}
                        onChange={(e) => setAccessNew(e.target.value)}
                      />
                      <button onClick={() => void accessSet()}>改密</button>
                    </div>
                    <div className="up-row">
                      <button
                        onClick={() =>
                          void api.accessLock().then(() => {
                            // 立刻回到锁屏（否则要等某个请求撞上 423 才切）
                            window.dispatchEvent(new CustomEvent('openminis:locked'))
                          })
                        }
                      >
                        立即上锁
                      </button>
                      <button onClick={() => void accessClear()}>清除密码</button>
                    </div>
                  </>
                )}
                {accessMsg && <div className="up-msg">{accessMsg}</div>}
              </>
            )}
          </div>
        )}
        <button
          className="nav-foot-item"
          onClick={() => props.onChangeView('settings')}
          title="设置"
        >
          <span className="ic">⚙</span>
          <span>设置</span>
        </button>
        <button
          className="nav-user"
          onClick={openUserPanel}
          title="进入密码 / 账号设置"
        >
          <span className="user-chip">👤</span>
          <span className="user-name">user</span>
        </button>
      </footer>
    </aside>
  )
}
