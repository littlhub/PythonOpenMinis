import { useCallback, useEffect, useState } from 'react'
import { ChatView } from './components/ChatView'
import { Sidebar, type ViewId } from './components/Sidebar'
import { SandboxView } from './components/SandboxView'
import { SettingsView } from './components/SettingsView'
import { WorkSpacesView } from './components/WorkSpacesView'
import { KnowledgeView } from './components/KnowledgeView'
import { MemoryView } from './components/MemoryView'
import { SkillsView } from './components/SkillsView'
import { ChannelsView } from './components/ChannelsView'
import { SchedulerView } from './components/SchedulerView'
import { SubagentsView } from './components/SubagentsView'
import { api } from './api'
import type { WorkspaceInfo } from './types'

/**
 * App shell. One left rail (Sidebar) holds navigation + workspaces + sessions
 * + 设置 + user, and the main pane swaps between views.
 */
export default function App() {
  const [view, setView] = useState<ViewId>('chat')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [, setWorkspaces] = useState<WorkspaceInfo[]>([])

  // hydrate from localStorage on mount
  useEffect(() => {
    const savedView = localStorage.getItem('openminis:view') as ViewId | null
    const savedSession = localStorage.getItem('openminis:active-session')
    if (savedView) setView(savedView)
    if (savedSession) setActiveId(savedSession)
  }, [])

  // persist
  useEffect(() => {
    localStorage.setItem('openminis:view', view)
  }, [view])
  useEffect(() => {
    if (activeId) localStorage.setItem('openminis:active-session', activeId)
    else localStorage.removeItem('openminis:active-session')
  }, [activeId])

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

  return (
    <div className={`app app-${view}`}>
      <Sidebar
        view={view}
        activeSessionId={activeId}
        onChangeView={setView}
        onSelectSession={selectSession}
        onCreateSession={createSession}
        onDeleteSession={deleteSession}
        onCreateWorkspace={createWorkspace}
        onDeleteWorkspace={deleteWorkspace}
      />

      <main className="app-main">
        {view === 'chat' && (
          <ChatView
            activeSessionId={activeId}
            onChangeSession={selectSession}
            onBackToChat={() => setView('chat')}
          />
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
