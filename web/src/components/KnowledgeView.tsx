import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { KnowledgeContent, KnowledgeItem } from '../types'

/**
 * KnowledgeView - 知识库页面
 *
 * 对本机知识库做全文检索：已安装技能包的 SKILL.md、记忆文件、以及本项目
 * 自带文档（README / PORTING*）。数据来自 /api/knowledge，只读。
 */
type Kind = '' | 'skill' | 'memory' | 'doc'

const KIND_ICON: Record<string, string> = { skill: '🧩', memory: '🧠', doc: '📄' }

export function KnowledgeView() {
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<Kind>('')
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [sources, setSources] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [content, setContent] = useState<KnowledgeContent | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const debounceRef = useRef<number | undefined>(undefined)

  const load = async (q: string, k: Kind) => {
    setLoading(true)
    setError('')
    try {
      const data = await api.knowledgeSearch(q, k)
      setItems(data.items)
      setSources(data.sources)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load('', '')
  }, [])

  // Debounced search on typing; kind switch searches immediately.
  useEffect(() => {
    window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => {
      void load(search, kind)
    }, search ? 250 : 0)
    return () => window.clearTimeout(debounceRef.current)
  }, [search, kind])

  const toggle = async (item: KnowledgeItem) => {
    if (openId === item.id) {
      setOpenId(null)
      setContent(null)
      return
    }
    setOpenId(item.id)
    setContent(null)
    setDetailLoading(true)
    try {
      setContent(await api.knowledgeContent(item.kind, item.source))
    } catch (e) {
      setContent({ kind: item.kind, name: item.source, title: item.title, source: item.source, modified: 0, content: `加载失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setDetailLoading(false)
    }
  }

  const kinds: { id: Kind; label: string }[] = [
    { id: '', label: '全部' },
    { id: 'skill', label: `技能 ${sources.skill ?? ''}` },
    { id: 'memory', label: `记忆 ${sources.memory ?? ''}` },
    { id: 'doc', label: `文档 ${sources.doc ?? ''}` },
  ]

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>📚 知识库</h2>
          <span className="kb-count">
            {loading ? '搜索中…' : `${items.length} 条结果`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}

        <div className="kb-search">
          <span className="kb-search-icon">🔍</span>
          <input
            type="text"
            placeholder="搜索技能、记忆、文档…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="kb-categories">
          {kinds.map((k) => (
            <button
              key={k.id || 'all'}
              className={`kb-cat-btn ${kind === k.id ? 'active' : ''}`}
              onClick={() => setKind(k.id)}
            >
              {k.label}
            </button>
          ))}
        </div>

        <div className="kb-list">
          {loading && <div className="kb-empty">搜索中…</div>}
          {!loading && items.length === 0 && (
            <div className="kb-empty">没有匹配的知识{search ? `（${search}）` : ''}</div>
          )}
          {!loading &&
            items.map((item) => (
              <div key={item.id} className="kb-item">
                <div className="kb-item-header">
                  <span className="kb-item-title">
                    {KIND_ICON[item.kind]} {item.title}
                  </span>
                  <span className="kb-item-date">{item.kindLabel} · {item.source}</span>
                </div>
                <p className="kb-item-summary">
                  {item.preview ? item.preview : '（空文档）'}
                </p>
                <div className="kb-item-footer">
                  <span className="kb-item-tag">{item.kindLabel}</span>
                  <button
                    className="kb-item-action"
                    onClick={() => void toggle(item)}
                  >
                    {openId === item.id ? '收起' : '查看全文'}
                  </button>
                </div>
                {openId === item.id && (
                  <div style={{ marginTop: 10 }}>
                    {detailLoading ? (
                      <div className="kb-empty">加载中…</div>
                    ) : (
                      <div className="memory-content">{content?.content}</div>
                    )}
                  </div>
                )}
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}
