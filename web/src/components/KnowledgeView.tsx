import { useState } from 'react'

/**
 * KnowledgeView - 知识库页面
 * 查看和管理 AI 的知识库，包括文档、FAQ、术语表等
 */
export function KnowledgeView() {
  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  // 示例知识库数据
  const categories = [
    { id: 'all', label: '全部', count: 48 },
    { id: 'docs', label: '文档', count: 12 },
    { id: 'faq', label: '常见问题', count: 18 },
    { id: 'terms', label: '术语表', count: 9 },
    { id: 'guide', label: '使用指南', count: 9 },
  ]

  const knowledgeItems = [
    { id: 1, title: 'OpenMinis 快速入门', category: 'guide', date: '2026-09-07', summary: '从零开始使用 OpenMinis AI Agent 的完整指南' },
    { id: 2, title: 'API 接口文档', category: 'docs', date: '2026-09-06', summary: 'REST API 和 WebSocket 接口的完整说明' },
    { id: 3, title: '如何配置模型', category: 'guide', date: '2026-09-05', summary: '支持的主流模型配置方法和最佳实践' },
    { id: 4, title: '常见问题汇总', category: 'faq', date: '2026-09-04', summary: '用户最常遇到的问题和解决方案' },
    { id: 5, title: '工作空间管理', category: 'docs', date: '2026-09-03', summary: '工作空间的创建、管理和权限设置' },
    { id: 6, title: 'MCP 协议接入', category: 'guide', date: '2026-09-02', summary: '如何使用 Model Context Protocol 扩展能力' },
  ]

  const filtered = knowledgeItems.filter((item) => {
    const matchSearch = item.title.includes(search) || item.summary.includes(search)
    const matchCategory = !selectedCategory || selectedCategory === 'all' || item.category === selectedCategory
    return matchSearch && matchCategory
  })

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>📚 知识库</h2>
          <span className="kb-count">{knowledgeItems.length} 条知识</span>
        </div>

        {/* 搜索框 */}
        <div className="kb-search">
          <span className="kb-search-icon">🔍</span>
          <input
            type="text"
            placeholder="搜索知识..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* 分类筛选 */}
        <div className="kb-categories">
          {categories.map((cat) => (
            <button
              key={cat.id}
              className={`kb-cat-btn ${selectedCategory === cat.id ? 'active' : ''}`}
              onClick={() => setSelectedCategory(cat.id === 'all' ? null : cat.id)}
            >
              {cat.label}
              <span className="kb-cat-count">{cat.count}</span>
            </button>
          ))}
        </div>

        {/* 知识列表 */}
        <div className="kb-list">
          {filtered.length === 0 ? (
            <div className="kb-empty">没有找到匹配的知识</div>
          ) : (
            filtered.map((item) => (
              <div key={item.id} className="kb-item">
                <div className="kb-item-header">
                  <span className="kb-item-title">{item.title}</span>
                  <span className="kb-item-date">{item.date}</span>
                </div>
                <p className="kb-item-summary">{item.summary}</p>
                <div className="kb-item-footer">
                  <span className="kb-item-tag">{getCategoryLabel(item.category)}</span>
                  <button className="kb-item-action">查看</button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function getCategoryLabel(cat: string): string {
  const labels: Record<string, string> = {
    docs: '文档',
    faq: '常见问题',
    terms: '术语表',
    guide: '使用指南',
  }
  return labels[cat] || cat
}
