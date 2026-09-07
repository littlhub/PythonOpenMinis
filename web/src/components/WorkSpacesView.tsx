import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { ChatSessionInfo, HealthInfo, WorkspaceInfo } from '../types'

/**
 * 顶栏"工作空间"tab:在工作空间和会话两个维度上管理。
 * - 顶部:工作空间列表(全部/各工作空间/未分组)
 * - 主体:当前选中工作空间下的会话列表
 * 提供新建/删除/重命名,以及直接跳到聊天。
 */
export function WorkSpacesView() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [allSessions, setAllSessions] = useState<ChatSessionInfo[]>([])
  const [activeWs, setActiveWs] = useState<string>('all')
  const [newName, setNewName] = useState('')
  const [newPath, setNewPath] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const [renamePath, setRenamePath] = useState('')

  const reloadWorkspaces = useCallback(async () => {
    try {
      const r = await api.workspaces()
      setWorkspaces(r.workspaces)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  const reloadAllSessions = useCallback(async () => {
    try {
      const r = await api.chatSessions() // 不传 folderId = 全部
      setAllSessions(r.sessions)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined)
    void reloadWorkspaces()
    void reloadAllSessions()
  }, [reloadAllSessions, reloadWorkspaces])

  const createWs = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const w = await api.workspaceCreate(name, '', newPath.trim())
      await reloadWorkspaces()
      setActiveWs(w.id)
      setNewName('')
      setNewPath('')
      setShowNew(false)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const deleteWs = async (id: string) => {
    if (!confirm('删除这个工作空间?其会话将变为未分组。')) return
    try {
      await api.workspaceDelete(id)
      if (activeWs === id) setActiveWs('all')
      await Promise.all([reloadWorkspaces(), reloadAllSessions()])
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const startRename = (w: WorkspaceInfo) => {
    setRenaming(w.id)
    setRenameVal(w.name)
    setRenamePath(w.path ?? '')
  }
  const commitRename = async () => {
    if (!renaming) return
    const name = renameVal.trim()
    if (!name) {
      setRenaming(null)
      return
    }
    try {
      await api.workspaceRename(renaming, name, '', renamePath.trim())
      await reloadWorkspaces()
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setRenaming(null)
    }
  }

  const createSessIn = async (folderId: string | null) => {
    try {
      const s = await api.chatCreate(folderId ?? undefined)
      await reloadAllSessions()
      // 直接跳到聊天并切到新会话:用 localStorage 留 id,聊天页读
      localStorage.setItem('openminis:active-session', s.id)
      window.location.hash = `#session=${s.id}`
      window.dispatchEvent(new CustomEvent('openminis:focus-session', { detail: s.id }))
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const deleteSess = async (id: string) => {
    if (!confirm('删除这个会话?消息记录也会一并删除。')) return
    try {
      await api.chatDelete(id)
      await reloadAllSessions()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const filteredSessions =
    activeWs === 'all'
      ? allSessions
      : activeWs === 'unfiled'
      ? allSessions.filter((s) => !s.folderId)
      : allSessions.filter((s) => s.folderId === activeWs)

  const wsNameOf = (id: string | null | undefined) => {
    if (!id) return '未分组'
    return workspaces.find((w) => w.id === id)?.name ?? '?'
  }

  return (
    <div className="view workspaces-view">
      <header className="ws-header">
        <h2>工作空间</h2>
        <button className="link" onClick={() => setShowNew((v) => !v)}>
          {showNew ? '取消' : '+ 新建工作空间'}
        </button>
      </header>
      {error && <div className="error">{error}</div>}
      {showNew && (
        <form
          className="ws-new-form"
          onSubmit={(e) => {
            e.preventDefault()
            void createWs()
          }}
        >
          <input
            autoFocus
            placeholder="工作空间名…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            placeholder="电脑目录(可选,绝对路径,例 D:\projects\foo)…"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            className="ws-path-input"
          />
          <button type="submit">创建</button>
        </form>
      )}

      <div className="ws-list">
        {(['all', 'unfiled', ...workspaces.map((w) => w.id)] as string[]).map((id) => {
          const isAll = id === 'all'
          const isUnfiled = id === 'unfiled'
          const w = isAll
            ? { id: 'all', name: '全部', icon: '⌂', path: '' }
            : isUnfiled
            ? { id: 'unfiled', name: '未分组', icon: '∅', path: '' }
            : {
                id,
                name: wsNameOf(id),
                icon: '📁',
                path: workspaces.find((x) => x.id === id)?.path ?? '',
              }
          const count = isAll
            ? allSessions.length
            : isUnfiled
            ? allSessions.filter((s) => !s.folderId).length
            : allSessions.filter((s) => s.folderId === id).length
          return (
            <button
              key={id}
              className={`ws-item ${activeWs === id ? 'active' : ''}`}
              onClick={() => setActiveWs(id)}
              title={w.path || w.name}
            >
              <span className="ws-icon">{w.icon}</span>
              <span className="ws-name">{w.name}</span>
              {w.path && (
                <span className="ws-path-pill" title={w.path}>
                  📂 {w.path.split(/[\\/]/).filter(Boolean).slice(-1)[0] || w.path}
                </span>
              )}
              <span className="ws-count">{count}</span>
              {!isAll && !isUnfiled && (
                <>
                  <span
                    className="link"
                    onClick={(e) => {
                      e.stopPropagation()
                      const w0 = workspaces.find((x) => x.id === id)
                      if (w0) startRename(w0)
                    }}
                  >
                    ✎
                  </span>
                  <span
                    className="link danger"
                    onClick={(e) => {
                      e.stopPropagation()
                      void deleteWs(id)
                    }}
                  >
                    ×
                  </span>
                </>
              )}
            </button>
          )
        })}
      </div>

      {renaming && (
        <div className="rename-overlay" onClick={() => setRenaming(null)}>
          <form
            className="rename-dialog"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault()
              void commitRename()
            }}
          >
            <h3>编辑工作空间</h3>
            <label className="muted small">名字</label>
            <input
              autoFocus
              value={renameVal}
              onChange={(e) => setRenameVal(e.target.value)}
            />
            <label className="muted small">电脑目录(留空清除)</label>
            <input
              placeholder="D:\projects\foo"
              value={renamePath}
              onChange={(e) => setRenamePath(e.target.value)}
            />
            <div className="row">
              <button type="button" className="link" onClick={() => setRenaming(null)}>
                取消
              </button>
              <button type="submit">保存</button>
            </div>
          </form>
        </div>
      )}

      <header className="ws-header">
        <h3>
          会话
          <span className="muted small">
            ({activeWs === 'all' ? '全部' : activeWs === 'unfiled' ? '未分组' : wsNameOf(activeWs)})
          </span>
        </h3>
        <button
          className="link"
          onClick={() =>
            void createSessIn(
              activeWs === 'all' || activeWs === 'unfiled' ? null : activeWs,
            )
          }
        >
          + 新会话
        </button>
      </header>

      <ul className="sess-list">
        {filteredSessions.length === 0 && (
          <li className="muted empty-line">尚无会话</li>
        )}
        {filteredSessions.map((s) => (
          <li key={s.id}>
            <div className="sess-meta">
              <div className="sess-title">{s.title}</div>
              <div className="sess-sub">
                <span className="muted small">{wsNameOf(s.folderId)}</span>
                <span className="muted small">· {s.lastMessage || '—'}</span>
              </div>
            </div>
            <span
              className="link danger"
              onClick={() => void deleteSess(s.id)}
              title="删除"
            >
              ×
            </span>
          </li>
        ))}
      </ul>

      {health && (
        <p className="empty mono small">
          data_dir: {health.data_dir}
        </p>
      )}
    </div>
  )
}
