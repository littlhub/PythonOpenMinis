import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MemoryDoc, MemoryFileInfo } from '../types'

/**
 * MemoryView - 记忆管理页面
 *
 * 直接编辑 <data_dir>/memory 下的 Markdown 文件（每日日志 YYYY-MM-DD.md、
 * GLOBAL.md、SOUL.md）。读写走后端 /api/system/memory，保存即写盘。
 */
function fmtTime(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Special files always float on top; daily logs follow, newest first. */
function rank(name: string): number {
  const base = name.toLowerCase()
  if (base === 'soul.md') return 0
  if (base === 'global.md') return 1
  return 2
}

export function MemoryView() {
  const [files, setFiles] = useState<MemoryFileInfo[]>([])
  const [dir, setDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [search, setSearch] = useState('')

  const [selected, setSelected] = useState<string | null>(null)
  const [doc, setDoc] = useState<MemoryDoc | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)

  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.memoryList()
      setFiles(data.files)
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

  const open = async (name: string) => {
    if (name === selected) return
    setSelected(name)
    setCreating(false)
    setDoc(null)
    setDraft('')
    setDirty(false)
    setNotice('')
    try {
      const d = await api.memoryGet(name)
      setDoc(d)
      setDraft(d.content)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    }
  }

  const save = async () => {
    if (!selected || busy) return
    setBusy(true)
    setNotice('')
    try {
      const d = await api.memoryPut(selected, draft)
      setDoc(d)
      setDraft(d.content)
      setDirty(false)
      setNotice(`已保存 ${selected}`)
      await load()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!selected || busy) return
    if (!window.confirm(`删除记忆文件「${selected}」？此操作不可恢复。`)) return
    setBusy(true)
    setNotice('')
    try {
      await api.memoryDelete(selected)
      setNotice(`已删除 ${selected}`)
      setSelected(null)
      setDoc(null)
      setDraft('')
      setDirty(false)
      await load()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const createFile = async () => {
    const name = newName.trim().replace(/\.md$/i, '')
    if (!name || busy) return
    const finalName = `${name}.md`
    if (files.some((f) => f.name === finalName)) {
      setNotice(`文件已存在：${finalName}`)
      return
    }
    setBusy(true)
    setNotice('')
    try {
      const d = await api.memoryPut(finalName, `# ${name}\n\n`)
      await load()
      setNewName('')
      setCreating(false)
      setSelected(finalName)
      setDoc(d)
      setDraft(d.content)
      setDirty(false)
      setNotice(`已创建 ${finalName}`)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const filtered = files
    .filter(
      (f) =>
        !search ||
        f.name.toLowerCase().includes(search.toLowerCase()) ||
        f.preview.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => rank(a.name) - rank(b.name) || b.mtime - a.mtime)

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>🧠 记忆管理</h2>
          <span className="kb-count">
            {loading ? '加载中…' : `${files.length} 个记忆文件`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}
        {notice && <div className="note note-info">{notice}</div>}
        {dir && <div className="pathline muted">记忆目录：{dir}</div>}

        {/* 列表 + 编辑器双栏 */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'stretch', marginTop: 10 }}>
          {/* 左栏：搜索 + 文件列表 */}
          <div style={{ width: 280, flex: '0 0 280px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="kb-search" style={{ flex: '0 0 auto' }}>
              <span className="kb-search-icon">🔍</span>
              <input
                type="text"
                placeholder="搜索记忆文件..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            {creating ? (
              <div className="create-task-form" style={{ display: 'flex', gap: 6 }}>
                <input
                  type="text"
                  placeholder="文件名（自动补 .md）"
                  value={newName}
                  autoFocus
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void createFile()
                  }}
                />
                <button className="btn-create" disabled={busy} onClick={() => void createFile()}>
                  创建
                </button>
              </div>
            ) : (
              <button
                className="memory-add-btn"
                disabled={busy}
                onClick={() => {
                  setCreating(true)
                  setNewName('')
                }}
              >
                ＋ 新建记忆文件
              </button>
            )}

            <div className="memory-list" style={{ flex: '1 1 auto', overflow: 'auto' }}>
              {loading && <div className="kb-empty">加载中…</div>}
              {!loading && filtered.length === 0 && (
                <div className="kb-empty">没有记忆文件</div>
              )}
              {!loading &&
                filtered.map((f) => {
                  const active = selected === f.name
                  return (
                    <div
                      key={f.name}
                      className="memory-item"
                      onClick={() => void open(f.name)}
                      style={{
                        cursor: 'pointer',
                        border: active ? '1px solid var(--accent, #2f6f4f)' : undefined,
                        background: active ? 'var(--bg-subtle, #f5f8f5)' : undefined,
                      }}
                    >
                      <div className="memory-item-header">
                        <span className="memory-date">
                          {f.name === 'SOUL.md' ? '人格' : f.name === 'GLOBAL.md' ? '全局' : '日志'}
                        </span>
                        <span className="memory-file-main" style={{ overflow: 'hidden' }}>
                          {f.name}
                        </span>
                      </div>
                      <div className="mf-meta muted" style={{ fontSize: 11 }}>
                        {fmtTime(f.mtime)} · {f.size} B
                      </div>
                      <p className="memory-content" style={{ fontSize: 12, marginBottom: 4 }}>
                        {f.preview || '（空）'}
                      </p>
                    </div>
                  )
                })}
            </div>
          </div>

          {/* 右栏：编辑器 */}
          <div style={{ flex: '1 1 auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {selected === null ? (
              <div className="kb-empty" style={{ padding: 40 }}>
                从左侧选择一个记忆文件，或新建一个开始编辑。
              </div>
            ) : (
              <>
                <div className="kb-item-header">
                  <span className="kb-item-title mono">{selected}</span>
                  <span className="kb-item-date muted">
                    {doc ? `${(doc.size / 1024).toFixed(1)} KB` : ''}
                  </span>
                  <span style={{ display: 'flex', gap: 6 }}>
                    <button
                      className="memory-action-btn"
                      disabled={busy || !dirty}
                      onClick={() => void save()}
                    >
                      {busy ? '保存中…' : '💾 保存'}
                    </button>
                    <button
                      className="memory-action-btn"
                      disabled={busy || !dirty}
                      onClick={() => {
                        setDraft(doc?.content ?? '')
                        setDirty(false)
                      }}
                    >
                      还原
                    </button>
                    <button
                      className="memory-action-btn"
                      disabled={busy}
                      onClick={() => void remove()}
                    >
                      删除
                    </button>
                  </span>
                </div>
                <textarea
                  className="memory-editor"
                  spellCheck={false}
                  value={draft}
                  onChange={(e) => {
                    setDraft(e.target.value)
                    setDirty(true)
                  }}
                  placeholder="输入 Markdown 记忆内容…"
                  style={{
                    flex: '1 1 auto',
                    minHeight: 320,
                    width: '100%',
                    resize: 'vertical',
                    fontFamily: 'monospace',
                    fontSize: 13,
                    lineHeight: 1.6,
                    padding: 10,
                    boxSizing: 'border-box',
                  }}
                />
                {dirty && <div className="muted" style={{ fontSize: 12 }}>有未保存的修改。</div>}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
