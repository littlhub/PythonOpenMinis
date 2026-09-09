import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MarketplaceSource } from '../types'

/**
 * MarketplaceView - 技能广场
 *
 * 内置技能/MCP 市场源卡片 + 粘贴 .zip 直链一键安装到本地技能库。
 */
export function MarketplaceView() {
  const [sources, setSources] = useState<MarketplaceSource[]>([])
  const [url, setUrl] = useState('')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.marketplaceSources()
      setSources(data.sources)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const doInstall = async () => {
    const link = url.trim()
    if (!link) return
    setBusy(true)
    setNotice('')
    setError('')
    try {
      const res = await api.marketplaceInstallUrl(link, force)
      setNotice(
        `已安装技能「${res.skill.name}」(${(res.bytes / 1024).toFixed(0)} KB)，可在技能页激活`,
      )
      setUrl('')
      setForce(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const skillSources = sources.filter((s) => s.kind === 'skill')
  const mcpSources = sources.filter((s) => s.kind === 'mcp')

  const card = (s: MarketplaceSource) => (
    <a
      key={s.id}
      className="market-card"
      href={s.url}
      target="_blank"
      rel="noreferrer"
    >
      <div className="market-card-head">
        <span className="market-card-icon">{s.kind === 'skill' ? '⚡' : '🔌'}</span>
        <span className="market-card-name">{s.name}</span>
        <span className="market-card-go">打开 ↗</span>
      </div>
      <div className="market-card-desc">{s.description}</div>
      <div className="kb-item-date mono">{s.url}</div>
    </a>
  )

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>🛍️ 技能广场</h2>
          <span className="kb-count">
            {loading ? '加载中…' : `${skillSources.length} 个技能市场 · ${mcpSources.length} 个 MCP 目录`}
          </span>
        </div>

        {error && <div className="note note-warn">{error}</div>}
        {notice && <div className="note note-ok">{notice}</div>}

        <h3 className="market-section-title">技能市场</h3>
        <div className="market-grid">{skillSources.map(card)}</div>

        <h3 className="market-section-title">MCP 目录</h3>
        <div className="market-grid">{mcpSources.map(card)}</div>

        <h3 className="market-section-title">从链接安装技能</h3>
        <p className="muted" style={{ margin: '4px 0 8px' }}>
          在市场里找到技能包的 <strong>.zip 直链</strong>（GitHub 仓库可用
          Code → Download ZIP 的链接），粘贴到这里一键装进本地技能库。
        </p>
        <div className="form-group">
          <div className="field-row">
            <input
              type="text"
              placeholder="https://…/skill-pack.zip"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !busy) void doInstall()
              }}
            />
            <button
              className="btn-create"
              disabled={busy || !url.trim()}
              onClick={() => void doInstall()}
            >
              {busy ? '下载安装中…' : '安装'}
            </button>
          </div>
          <label className="cfg-row" style={{ marginTop: 6 }}>
            <input
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
            />
            <span className="cfg-label">同名技能已存在时覆盖</span>
          </label>
        </div>
      </div>
    </div>
  )
}
