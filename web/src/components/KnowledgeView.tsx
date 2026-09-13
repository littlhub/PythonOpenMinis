import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  KnowledgeCategoryId,
  KnowledgeContent,
  KnowledgeGraph,
  KnowledgeItem,
  KnowledgeKindInfo,
} from '../types'

/**
 * KnowledgeView - 知识库页面
 *
 * 两个视图：
 * - 列表：按 概念/实体/来源/分析/模板 五类分组（可折叠），外加 记忆 / 技能 / 文档；
 * - 图谱：知识文档之间的 [[双链]] 关系图 —— 节点按分类着色，连线 = 引用。
 *
 * 数据来自 /api/knowledge 与 /api/knowledge/graph，只读。
 */
type Kind = '' | 'knowledge' | 'memory' | 'skill' | 'doc'
type Mode = 'list' | 'graph'

const KIND_ICON: Record<string, string> = {
  skill: '🧩',
  memory: '🧠',
  knowledge: '📚',
  doc: '📄',
}

/** 五分类配色（中间调，浅色/深色主题下都读得清）。 */
const CATEGORY_COLOR: Record<string, string> = {
  concepts: '#4a7fb5',
  entities: '#b5794a',
  sources: '#5f9a6a',
  analysis: '#8b6bb5',
  templates: '#c0567a',
  other: '#8a8a8a',
}

const FALLBACK_KINDS: KnowledgeKindInfo[] = [
  { id: 'concepts', label: '概念', dir: '概念', desc: '可复用的概念、方法、原理、风格、术语' },
  { id: 'entities', label: '实体', dir: '实体', desc: '具体的人/项目/工具/作品/服务' },
  { id: 'sources', label: '来源', dir: '来源', desc: '外部资料：文章、文档、链接、论文、数据集' },
  { id: 'analysis', label: '分析', dir: '分析', desc: '分析报告、结论、复盘、对比与决策记录' },
  { id: 'templates', label: '模板', dir: '模板', desc: '可复用的模板、骨架、清单、提示词' },
]

const categoryColor = (id?: string) => CATEGORY_COLOR[id ?? 'other'] ?? '#8a8a8a'

interface Group {
  key: string
  label: string
  color: string
  icon: string
  items: KnowledgeItem[]
}

export function KnowledgeView() {
  const [mode, setMode] = useState<Mode>('list')
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<Kind>('')
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [sources, setSources] = useState<Record<string, number>>({})
  const [kinds, setKinds] = useState<KnowledgeKindInfo[]>(FALLBACK_KINDS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [content, setContent] = useState<KnowledgeContent | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const debounceRef = useRef<number | undefined>(undefined)

  // graph
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)

  const load = async (q: string, k: Kind) => {
    setLoading(true)
    setError('')
    try {
      const data = await api.knowledgeSearch(q, k)
      setItems(data.items)
      setSources(data.sources)
      if (data.knowledgeKinds?.length) setKinds(data.knowledgeKinds)
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
    debounceRef.current = window.setTimeout(
      () => {
        void load(search, kind)
      },
      search ? 250 : 0,
    )
    return () => window.clearTimeout(debounceRef.current)
  }, [search, kind])

  // 图谱按需加载（切到图谱、或知识库有写入后回来时重新取）
  useEffect(() => {
    if (mode !== 'graph' || graph) return
    setGraphLoading(true)
    void api
      .knowledgeGraph()
      .then((g) => setGraph(g))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setGraphLoading(false))
  }, [mode, graph])

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
      setContent({
        kind: item.kind,
        name: item.source,
        title: item.title,
        source: item.source,
        modified: 0,
        content: `加载失败：${e instanceof Error ? e.message : String(e)}`,
      })
    } finally {
      setDetailLoading(false)
    }
  }

  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  /** 知识按五类分组，其余来源（记忆/技能/文档）各成一组。 */
  const groups = useMemo<Group[]>(() => {
    const out: Group[] = []
    const bucket = new Map<string, KnowledgeItem[]>()
    for (const it of items) {
      const key = it.kind === 'knowledge' ? `cat:${it.category ?? 'other'}` : `kind:${it.kind}`
      if (!bucket.has(key)) bucket.set(key, [])
      bucket.get(key)!.push(it)
    }
    for (const k of kinds) {
      const list = bucket.get(`cat:${k.id}`)
      if (list?.length) {
        out.push({ key: `cat:${k.id}`, label: k.label, color: categoryColor(k.id), icon: '📚', items: list })
      }
    }
    const other = bucket.get('cat:other')
    if (other?.length) {
      out.push({ key: 'cat:other', label: '未归类', color: categoryColor('other'), icon: '📚', items: other })
    }
    for (const id of ['memory', 'skill', 'doc'] as const) {
      const list = bucket.get(`kind:${id}`)
      if (list?.length) {
        out.push({
          key: `kind:${id}`,
          label: { memory: '记忆', skill: '技能', doc: '文档' }[id],
          color: '#8a8a8a',
          icon: KIND_ICON[id],
          items: list,
        })
      }
    }
    return out
  }, [items, kinds])

  const kindTabs: { id: Kind; label: string }[] = [
    { id: '', label: '全部' },
    { id: 'knowledge', label: `知识 ${sources.knowledge ?? ''}` },
    { id: 'memory', label: `记忆 ${sources.memory ?? ''}` },
    { id: 'skill', label: `技能 ${sources.skill ?? ''}` },
    { id: 'doc', label: `文档 ${sources.doc ?? ''}` },
  ]

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>📚 知识库</h2>
          <div className="kb-modes">
            <button
              className={`kb-mode-btn ${mode === 'list' ? 'active' : ''}`}
              onClick={() => setMode('list')}
            >
              列表
            </button>
            <button
              className={`kb-mode-btn ${mode === 'graph' ? 'active' : ''}`}
              onClick={() => setMode('graph')}
            >
              图谱
            </button>
          </div>
          <span className="kb-count">
            {loading ? '搜索中…' : `${items.length} 条结果`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}

        {mode === 'list' ? (
          <>
            <div className="kb-search">
              <span className="kb-search-icon">🔍</span>
              <input
                type="text"
                placeholder="搜索技能、记忆、知识、文档…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <div className="kb-categories">
              {kindTabs.map((k) => (
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
                groups.map((g) => {
                  const isCollapsed = collapsed.has(g.key)
                  return (
                    <div key={g.key} className="kb-group">
                      <button className="kb-group-head" onClick={() => toggleGroup(g.key)}>
                        <span className="caret">{isCollapsed ? '›' : '⌄'}</span>
                        <span className="kb-dot" style={{ background: g.color }} />
                        <span className="kb-group-label">{g.label}</span>
                        <span className="kb-group-count">{g.items.length}</span>
                      </button>
                      {!isCollapsed &&
                        g.items.map((item) => (
                          <div key={item.id} className="kb-item">
                            <div className="kb-item-header">
                              <span className="kb-item-title">
                                {KIND_ICON[item.kind]} {item.title}
                              </span>
                              <span className="kb-item-date">
                                {item.categoryLabel ?? item.kindLabel} · {item.source}
                              </span>
                            </div>
                            <p className="kb-item-summary">
                              {item.preview ? item.preview : '（空文档）'}
                            </p>
                            <div className="kb-item-footer">
                              <span className="kb-item-tag">{item.categoryLabel ?? item.kindLabel}</span>
                              <button className="kb-item-action" onClick={() => void toggle(item)}>
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
                  )
                })}
            </div>
          </>
        ) : (
          <KnowledgeGraphView
            graph={graph}
            loading={graphLoading}
            kinds={kinds}
            onRefresh={() => setGraph(null)}
          />
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* 图谱：节点 = 文档（按分类着色），连线 = [[双链]]                            */
/* ------------------------------------------------------------------------- */
const GW = 720
const GH = 460
const CLUSTER_R = 132
const NODE_R = 7

interface Placed {
  id: string
  label: string
  category: string
  color: string
  x: number
  y: number
  path: string
}

function KnowledgeGraphView({
  graph,
  loading,
  kinds,
  onRefresh,
}: {
  graph: KnowledgeGraph | null
  loading: boolean
  kinds: KnowledgeKindInfo[]
  onRefresh: () => void
}) {
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [content, setContent] = useState<KnowledgeContent | null>(null)

  const placed = useMemo<Placed[]>(() => {
    if (!graph) return []
    const order = kinds.map((k) => k.id as KnowledgeCategoryId).concat(['other'])
    const byCat = new Map<string, KnowledgeGraph['nodes']>()
    for (const n of graph.nodes) {
      const c = n.category || 'other'
      if (!byCat.has(c)) byCat.set(c, [])
      byCat.get(c)!.push(n)
    }
    const cats = order.filter((c) => byCat.get(c)?.length)
    const out: Placed[] = []
    const cx = GW / 2
    const cy = GH / 2
    cats.forEach((cat, ci) => {
      const angle = (ci / Math.max(1, cats.length)) * Math.PI * 2 - Math.PI / 2
      const ccx = cx + Math.cos(angle) * CLUSTER_R
      const ccy = cy + Math.sin(angle) * CLUSTER_R
      const nodes = byCat.get(cat)!
      const ring = Math.min(52, 13 * nodes.length)
      nodes.forEach((n, ni) => {
        const a = (ni / Math.max(1, nodes.length)) * Math.PI * 2 - Math.PI / 2
        out.push({
          id: n.id,
          label: n.label,
          category: cat,
          color: categoryColor(cat),
          x: ccx + Math.cos(a) * ring,
          y: ccy + Math.sin(a) * ring,
          path: n.path,
        })
      })
    })
    return out
  }, [graph, kinds])

  const pos = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed])
  const isolated = useMemo(() => {
    if (!graph) return 0
    const linked = new Set<string>()
    for (const l of graph.links) {
      linked.add(l.source)
      linked.add(l.target)
    }
    return graph.nodes.filter((n) => !linked.has(n.id)).length
  }, [graph])

  const open = async (path: string) => {
    if (openPath === path) {
      setOpenPath(null)
      setContent(null)
      return
    }
    setOpenPath(path)
    setContent(null)
    try {
      setContent(await api.knowledgeContent('knowledge', path))
    } catch (e) {
      setContent(null)
      console.warn(String(e))
    }
  }

  if (loading && !graph) return <div className="kb-empty">正在生成图谱…</div>
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="kb-empty">
        知识库还没有文档。让助手把资料写进知识库（概念/实体/来源/分析/模板）后，这里会出现节点。
      </div>
    )
  }

  return (
    <div className="kb-graph-wrap">
      <div className="kb-legend">
        {kinds.map((k) => (
          <span key={k.id} className="kb-legend-item">
            <span className="kb-dot" style={{ background: categoryColor(k.id) }} />
            {k.label}
          </span>
        ))}
        <span className="kb-legend-item">
          <span className="kb-dot" style={{ background: categoryColor('other') }} />
          其他
        </span>
        <span className="kb-legend-meta">
          {graph.nodes.length} 个文档 · {graph.links.length} 条引用
          {isolated > 0 ? ` · ${isolated} 个未连接` : ''}
        </span>
        <button className="kb-item-action" onClick={onRefresh}>
          刷新
        </button>
      </div>

      <svg className="kb-graph" viewBox={`0 0 ${GW} ${GH}`} role="img" aria-label="知识图谱">
        {graph.links.map((l, i) => {
          const a = pos.get(l.source)
          const b = pos.get(l.target)
          if (!a || !b) return null
          return (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="#9aa0a6"
              strokeOpacity={0.45}
              strokeWidth={1.2}
            />
          )
        })}
        {placed.map((p) => (
          <g
            key={p.id}
            className={`kb-node ${openPath === p.path ? 'active' : ''}`}
            onClick={() => void open(p.path)}
          >
            <circle cx={p.x} cy={p.y} r={NODE_R} fill={p.color} />
            <text x={p.x} y={p.y + NODE_R + 11} textAnchor="middle" className="kb-node-label">
              {p.label.length > 9 ? `${p.label.slice(0, 9)}…` : p.label}
            </text>
          </g>
        ))}
      </svg>

      {openPath && (
        <div className="kb-graph-detail">
          <div className="kb-item-header">
            <span className="kb-item-title">📚 {content?.title ?? openPath}</span>
            <span className="kb-item-date">{content?.categoryLabel ?? ''}</span>
          </div>
          <div className="memory-content">{content?.content ?? '加载中…'}</div>
        </div>
      )}
    </div>
  )
}
