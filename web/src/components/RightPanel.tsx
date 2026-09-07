import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { FsNode, HistoryEntry } from '../types'

interface RightPanelProps {
  activeSessionId: string | null
  activeWorkspaceId?: string | null
  onPickHistory: (sessionId: string) => void
  onClose?: () => void
}

type Tab = 'files' | 'history'

export function RightPanel({
  activeSessionId,
  activeWorkspaceId,
  onPickHistory,
  onClose,
}: RightPanelProps) {
  const [tab, setTab] = useState<Tab>('files')

  return (
    <aside className="right-panel">
      <div className="rp-head">
        <div className="rp-tabs">
          <button
            className={tab === 'files' ? 'active' : ''}
            onClick={() => setTab('files')}
          >
            项目文件
          </button>
          <button
            className={tab === 'history' ? 'active' : ''}
            onClick={() => setTab('history')}
          >
            历史提问
          </button>
        </div>
        {onClose && (
          <button
            className="rp-close"
            title="收起右侧栏"
            aria-label="收起右侧栏"
            onClick={onClose}
          >
            ❯
          </button>
        )}
      </div>
      {tab === 'files' ? (
        <FilesTab workspace={activeWorkspaceId} />
      ) : (
        <HistoryTab
          activeSessionId={activeSessionId}
          onPickHistory={onPickHistory}
        />
      )}
    </aside>
  )
}

// -----------------------------------------------------------------------
function FilesTab({ workspace }: { workspace?: string | null }) {
  const [root, setRoot] = useState<{ root: string; name: string; exists: boolean } | null>(null)
  const [tree, setTree] = useState<FsNode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openDirs, setOpenDirs] = useState<Set<string>>(new Set(['']))
  const [preview, setPreview] = useState<{
    path: string
    name: string
    content: string
    truncated: boolean
    lines: number
    size: number
  } | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const reload = useCallback(async () => {
    try {
      const ws = workspace || undefined
      const [r, t] = await Promise.all([
        api.fsRoot(ws),
        api.fsTree('', 3, ws),
      ])
      setRoot(r)
      setTree(t)
      setError(null)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [workspace])

  useEffect(() => {
    void reload()
  }, [reload])

  const toggle = (path: string) =>
    setOpenDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const openFile = async (path: string) => {
    setLoading(true)
    setPreviewError(null)
    try {
      const ws = workspace || undefined
      const r = await api.fsRead(path, 80_000, ws)
      setPreview(r)
    } catch (e) {
      setPreview(null)
      setPreviewError(String((e as Error).message))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="rp-body files-tab">
      {error && <div className="rp-error">{error}</div>}
      {!tree && !error && <div className="rp-empty">加载中…</div>}
      {tree && (
        <>
          <div className="fs-root-line">
            <span className="muted">工作目录:</span>{' '}
            <code title={root?.root}>{root?.name}</code>
            <button
              className="link"
              onClick={() => void reload()}
              title="刷新"
            >
              ⟳
            </button>
          </div>
          <div className="fs-tree">
            {tree.children?.length ? (
              <FsTreeNode
                node={tree}
                relPath=""
                depth={0}
                openDirs={openDirs}
                toggle={toggle}
                onOpen={openFile}
              />
            ) : (
              <div className="rp-empty">工作目录为空</div>
            )}
          </div>
          {preview && (
            <div className="fs-preview">
              <div className="fs-preview-head">
                <span className="fs-preview-name">{preview.name}</span>
                <span className="muted">
                  {preview.lines} 行 · {preview.size} B
                  {preview.truncated ? ' · 已截断' : ''}
                </span>
                <button className="link" onClick={() => setPreview(null)}>
                  关闭
                </button>
              </div>
              <pre>{preview.content}</pre>
            </div>
          )}
          {previewError && <div className="rp-error">{previewError}</div>}
          {loading && <div className="rp-empty">读取中…</div>}
        </>
      )}
    </div>
  )
}

interface FsTreeNodeProps {
  node: FsNode
  relPath: string
  depth: number
  openDirs: Set<string>
  toggle: (path: string) => void
  onOpen: (path: string) => void
}

function FsTreeNode({
  node,
  relPath,
  depth,
  openDirs,
  toggle,
  onOpen,
}: FsTreeNodeProps) {
  const isOpen = openDirs.has(relPath)
  if (node.isDir) {
    return (
      <div className="fs-dir">
        <button
          className="fs-row fs-dir-row"
          style={{ paddingLeft: 8 + depth * 12 }}
          onClick={() => toggle(relPath)}
        >
          <span className="fs-caret">{isOpen ? '▾' : '▸'}</span>
          <span className="fs-icon">📁</span>
          <span className="fs-label">{node.name}</span>
        </button>
        {isOpen && node.children && (
          <div className="fs-dir-children">
            {node.children.map((c) => (
              <FsTreeNode
                key={c.path}
                node={c}
                relPath={c.path}
                depth={depth + 1}
                openDirs={openDirs}
                toggle={toggle}
                onOpen={onOpen}
              />
            ))}
          </div>
        )}
      </div>
    )
  }
  return (
    <button
      className="fs-row fs-file-row"
      style={{ paddingLeft: 8 + depth * 12 + 14 }}
      onClick={() => void onOpen(relPath)}
      title={relPath}
    >
      <span className="fs-icon">{iconFor(node.name)}</span>
      <span className="fs-label">{node.name}</span>
      <span className="fs-size">{humanSize(node.size)}</span>
    </button>
  )
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function iconFor(name: string): string {
  if (name.endsWith('.py')) return '🐍'
  if (name.endsWith('.ts') || name.endsWith('.tsx')) return '📘'
  if (name.endsWith('.js') || name.endsWith('.jsx')) return '📒'
  if (name.endsWith('.json') || name.endsWith('.yaml') || name.endsWith('.yml')) return '⚙️'
  if (name.endsWith('.md') || name.endsWith('.txt')) return '📄'
  if (name.endsWith('.css') || name.endsWith('.html')) return '🎨'
  return '📄'
}

// -----------------------------------------------------------------------
function HistoryTab({
  activeSessionId,
  onPickHistory,
}: {
  activeSessionId: string | null
  onPickHistory: (sessionId: string) => void
}) {
  const [items, setItems] = useState<HistoryEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const h = await api.history()
      setItems(h)
      setError(null)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload, activeSessionId])

  if (error) return <div className="rp-error">{error}</div>
  if (!items) return <div className="rp-empty">加载历史中…</div>
  if (items.length === 0)
    return <div className="rp-empty">还没有提问记录</div>

  return (
    <div className="rp-body history-tab">
      <div className="history-header">
        <span className="muted">{items.length} 条历史提问</span>
        <button className="link" onClick={() => void reload()}>
          ⟳
        </button>
      </div>
      <ul className="history-list">
        {items.map((h) => (
          <li
            key={h.messageId}
            className={activeSessionId === h.sessionId ? 'active' : ''}
          >
            <button
              className="history-row"
              onClick={() => onPickHistory(h.sessionId)}
              title={new Date(h.createdAt).toLocaleString()}
            >
              <span className="history-text">{h.text}</span>
              <span className="history-meta">{h.sessionTitle}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}