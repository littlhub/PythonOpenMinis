import { useCallback, useEffect, useState } from 'react'
import { ChatView } from './components/ChatView'
import { Sidebar, type ViewId } from './components/Sidebar'
import { SandboxView } from './components/SandboxView'
import { SettingsView } from './components/SettingsView'
import { WorkSpacesView } from './components/WorkSpacesView'
import { KnowledgeView } from './components/KnowledgeView'
import { MemoryView } from './components/MemoryView'
import { SkillsView } from './components/SkillsView'
import { MarketplaceView } from './components/MarketplaceView'
import { ChannelsView } from './components/ChannelsView'
import { SchedulerView } from './components/SchedulerView'
import { SubagentsView } from './components/SubagentsView'
import { LockScreen } from './components/LockScreen'
import { ImageLightbox } from './components/ImageLightbox'
import { api } from './api'
import { applyBackground, hydrateBackground } from './theme'
import type { WorkspaceInfo } from './types'

/**
 * App shell. One left rail (Sidebar) holds navigation + workspaces + sessions
 * + 设置 + user, and the main pane swaps between views.
 */
export default function App() {
  const [view, setView] = useState<ViewId>('chat')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [, setWorkspaces] = useState<WorkspaceInfo[]>([])
  // 左边栏折叠：点「OpenMinis」图标切换（存本地，下次打开保持）。
  const [railCollapsed, setRailCollapsed] = useState(false)

  // 访问闸门：设了「进入密码」且未解锁时，整个界面换成锁屏 —— 用户明确要求
  // 「锁了就该只看到输入框」。'checking' 期间也不渲染界面，避免先漏一眼内容。
  const [access, setAccess] = useState<'checking' | 'locked' | 'open'>('checking')

  useEffect(() => {
    let alive = true
    void api
      .accessStatus()
      .then((s) => {
        if (alive) setAccess(s.hasPassword && s.locked ? 'locked' : 'open')
      })
      // 状态读不到（后端没起/接口异常）不该把用户关在门外
      .catch(() => {
        if (alive) setAccess('open')
      })
    return () => {
      alive = false
    }
  }, [])

  // 令牌过期时任意请求会拿到 423，request() 广播这个事件 → 回到锁屏
  useEffect(() => {
    const onLocked = () => setAccess('locked')
    window.addEventListener('openminis:locked', onLocked)
    return () => window.removeEventListener('openminis:locked', onLocked)
  }, [])

  // hydrate from localStorage on mount
  useEffect(() => {
    const savedView = localStorage.getItem('openminis:view') as ViewId | null
    const savedSession = localStorage.getItem('openminis:active-session')
    if (savedView) setView(savedView)
    if (savedSession) setActiveId(savedSession)
    if (localStorage.getItem('openminis:rail-collapsed') === '1') {
      setRailCollapsed(true)
    }
  }, [])

  // 背景偏好来自后端 config（不是 localStorage）—— 它跟着数据目录走，
  // 同一份配置换个浏览器打开外观一致。设置页改完发事件即时生效，不必刷新。
  useEffect(() => {
    if (access !== 'open') return
    void hydrateBackground()
    const onAppearance = (e: Event) =>
      applyBackground((e as CustomEvent<string>).detail)
    window.addEventListener('openminis:appearance', onAppearance as EventListener)
    return () =>
      window.removeEventListener('openminis:appearance', onAppearance as EventListener)
  }, [access])

  // persist
  useEffect(() => {
    localStorage.setItem('openminis:view', view)
  }, [view])
  useEffect(() => {
    if (activeId) localStorage.setItem('openminis:active-session', activeId)
    else localStorage.removeItem('openminis:active-session')
  }, [activeId])

  const toggleRail = useCallback(() => {
    setRailCollapsed((v) => {
      const next = !v
      localStorage.setItem('openminis:rail-collapsed', next ? '1' : '0')
      return next
    })
  }, [])

  // listen for "focus session" events from sibling views
  useEffect(() => {
    const onFocus = (e: Event) => {
      const sid = (e as CustomEvent<string>).detail
      if (sid) {
        setActiveId(sid)
        setView('chat')
      }
    }
    window.addEventListener('openminis:focus-session', onFocus as EventListener)
    return () =>
      window.removeEventListener(
        'openminis:focus-session',
        onFocus as EventListener,
      )
  }, [])

  const selectSession = useCallback((id: string) => {
    setActiveId(id)
  }, [])

  const createSession = useCallback(async (folderId?: string | null) => {
    const created = await api.chatCreate(folderId ?? undefined)
    setActiveId(created.id)
  }, [])

  const deleteSession = useCallback(async (id: string) => {
    await api.chatDelete(id)
    setActiveId((cur) => (cur === id ? null : cur))
  }, [])

  const createWorkspace = useCallback(
    async (name: string, path?: string) => {
      try {
        const w = await api.workspaceCreate(name, '', path)
        setWorkspaces((prev) => [...prev, w])
      } catch (e) {
        console.warn('workspace create failed', e)
        throw e
      }
    },
    [],
  )

  const deleteWorkspace = useCallback(async (id: string) => {
    await api.workspaceDelete(id)
    setWorkspaces((prev) => prev.filter((w) => w.id !== id))
  }, [])

  // 锁着（或还没问清楚）就只渲染密码框 —— 侧栏、会话、内容一概不挂载。
  if (access !== 'open') {
    return <LockScreen mode={access} onUnlocked={() => setAccess('open')} />
  }

  return (
    <div className={`app app-${view}${railCollapsed ? ' rail-collapsed' : ''}`}>
      <ImageLightbox />
      <Sidebar
        view={view}
        activeSessionId={activeId}
        collapsed={railCollapsed}
        onToggleCollapsed={toggleRail}
        onChangeView={setView}
        onSelectSession={selectSession}
        onCreateSession={createSession}
        onDeleteSession={deleteSession}
        onCreateWorkspace={createWorkspace}
        onDeleteWorkspace={deleteWorkspace}
      />

      <main className="app-main">
        {view === 'chat' && (
          <ChatView activeSessionId={activeId} onChangeSession={selectSession} />
        )}
        {view === 'knowledge' && (
          <div className="pane">
            <KnowledgeView />
          </div>
        )}
        {view === 'memory' && (
          <div className="pane">
            <MemoryView />
          </div>
        )}
        {view === 'skills' && (
          <div className="pane">
            <SkillsView />
          </div>
        )}
        {view === 'market' && (
          <div className="pane">
            <MarketplaceView />
          </div>
        )}
        {view === 'channels' && (
          <div className="pane">
            <ChannelsView />
          </div>
        )}
        {view === 'scheduler' && (
          <div className="pane">
            <SchedulerView />
          </div>
        )}
        {view === 'projects' && (
          <div className="pane">
            <WorkSpacesView />
          </div>
        )}
        {view === 'workspaces' && (
          <div className="pane">
            <SubagentsView />
          </div>
        )}
        {view === 'sandbox' && (
          <div className="pane">
            <SandboxView />
          </div>
        )}
        {view === 'settings' && (
          <div className="pane">
            <SettingsView />
          </div>
        )}
      </main>
    </div>
  )
}
