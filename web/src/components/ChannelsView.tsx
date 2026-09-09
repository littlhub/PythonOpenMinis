import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ProviderInfo, SkillToolInfo } from '../types'

/**
 * ChannelsView - 通道管理页面
 *
 * 展示引擎实际接入的外部能力通道，全部来自真实接口：
 *  - 模型服务通道（providers，/api/settings）
 *  - 工具通道（agent 当前注册的工具，/api/skills）
 *  - 本地接入（WebSocket、技能包、记忆文件、会话、存储目录）
 */
type ChannelStatus = 'ok' | 'off' | 'info'

interface ChannelRow {
  id: string
  name: string
  icon: string
  desc: string
  status: ChannelStatus
  statusText: string
}

function statusClass(s: ChannelStatus): string {
  if (s === 'ok') return 'connected'
  if (s === 'off') return 'disconnected'
  return ''
}

function ProviderCard({ p }: { p: ProviderInfo }) {
  const ready = p.hasKey
  return (
    <div className={`channel-card ${ready ? 'connected' : 'disconnected'}`}>
      <div className="channel-icon">{p.engine === 'anthropic' ? '🟣' : '🔷'}</div>
      <div className="channel-info">
        <div className="channel-name">
          {p.label}
          {p.isActive && <span className="kb-count"> 当前使用</span>}
        </div>
        <div className="channel-desc">
          {p.engine || '引擎未移植'} · {p.model || p.defaultModel}
          {ready ? '' : ' · 未填 API Key'}
        </div>
      </div>
      <div className="channel-status">
        <span className={`status-dot ${ready ? 'connected' : 'disconnected'}`} />
        <span className="status-text">{ready ? '已配置' : '未配置'}</span>
      </div>
      <div className="channel-info" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        <div className="muted" style={{ fontSize: 11 }} title={p.baseUrl || ''}>
          {p.baseUrl || '默认 Base URL'}
        </div>
      </div>
    </div>
  )
}

function ToolCard({ t }: { t: SkillToolInfo }) {
  return (
    <div className="channel-card connected">
      <div className="channel-icon">🧰</div>
      <div className="channel-info">
        <div className="channel-name mono">{t.name}</div>
        <div className="channel-desc" title={t.description}>
          {t.description.slice(0, 90) || '（无描述）'}
        </div>
      </div>
      <div className="channel-status">
        <span className="status-dot connected" />
        <span className="status-text">可用</span>
      </div>
      <div className="channel-info" style={{ maxWidth: 160, fontSize: 11 }}
           title={Object.keys(t.parameters).join('、')}>
        <div className="muted">参数：{Object.keys(t.parameters).slice(0, 3).join('、') || '—'}</div>
      </div>
    </div>
  )
}

export function ChannelsView() {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [tools, setTools] = useState<SkillToolInfo[]>([])
  const [locals, setLocals] = useState<ChannelRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const [settings, skills, memory, health, sessions] = await Promise.all([
          api.settingsGet(),
          api.skillsList(),
          api.memoryList(),
          api.health(),
          api.chatSessions(),
        ])
        setProviders(settings.providers)
        setTools(skills.tools)
        setLocals([
          {
            id: 'local:ws',
            name: 'Web 会话服务',
            icon: '🔌',
            desc: `${location.host} — WebSocket /ws 与 REST /api`,
            status: 'ok',
            statusText: '运行中',
          },
          {
            id: 'local:skills',
            name: '技能包',
            icon: '🧩',
            desc: `${skills.skills.length} 个已安装 · ${skills.dir}`,
            status: 'info',
            statusText: `${skills.skills.length} 个`,
          },
          {
            id: 'local:memory',
            name: '记忆文件',
            icon: '🧠',
            desc: `${memory.files.length} 个 Markdown · ${memory.dir}`,
            status: 'info',
            statusText: `${memory.files.length} 个`,
          },
          {
            id: 'local:chats',
            name: '会话存储',
            icon: '💬',
            desc: `${sessions.sessions.length} 个历史会话`,
            status: 'info',
            statusText: `${sessions.sessions.length} 个`,
          },
          {
            id: 'local:data',
            name: '数据目录',
            icon: '🗄️',
            desc: health.data_dir,
            status: 'info',
            statusText: health.status,
          },
          {
            id: 'local:workspace',
            name: '工作区',
            icon: '📂',
            desc: health.workspace,
            status: 'info',
            statusText: '沙箱根',
          },
        ])
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>📡 通道</h2>
          <span className="kb-count">
            {loading
              ? '检测中…'
              : `${providers.filter((p) => p.hasKey).length} 个模型通道 · ${tools.length} 个工具通道`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}
        <p className="channels-desc">
          引擎实际接入的外部通道。模型服务与工具可在「设置」中配置；以下状态来自当前运行环境。
        </p>

        {loading ? (
          <div className="kb-empty">检测通道状态…</div>
        ) : (
          <>
            <div className="set-section" style={{ marginTop: 8 }}>
              <div className="set-title">模型服务通道</div>
              <div className="channels-list">
                {providers.map((p) => (
                  <ProviderCard key={p.id} p={p} />
                ))}
              </div>
            </div>

            <div className="set-section">
              <div className="set-title">工具通道（Agent 可调用）</div>
              <div className="channels-list">
                {tools.map((t) => (
                  <ToolCard key={t.name} t={t} />
                ))}
              </div>
            </div>

            <div className="set-section">
              <div className="set-title">本地接入</div>
              <div className="channels-list">
                {locals.map((c) => (
                  <div key={c.id} className={`channel-card ${statusClass(c.status)}`}>
                    <div className="channel-icon">{c.icon}</div>
                    <div className="channel-info">
                      <div className="channel-name">{c.name}</div>
                      <div className="channel-desc" title={c.desc}>
                        {c.desc}
                      </div>
                    </div>
                    <div className="channel-status">
                      <span className={`status-dot ${statusClass(c.status)}`} />
                      <span className="status-text">{c.statusText}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
