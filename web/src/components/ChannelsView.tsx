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

/**
 * 机器人通道（微信 / QQ）。
 *
 * 本体还没接进来 —— 它们是「插件」形态：安装对应技能包之后由插件负责登录、
 * 收发消息，引擎这边只提供模型与会话。所以这里不做成写死的假开关，而是照技能
 * 目录的真实内容判断状态，装上插件后状态自己就变了。
 */
interface BotChannel {
  id: string
  name: string
  icon: string
  desc: string
  /** 需要安装的技能/插件名，与技能目录里的名字比对。 */
  plugin: string
}

const BOT_CHANNELS: BotChannel[] = [
  {
    id: 'bot:wechat',
    name: '微信',
    icon: '💬',
    desc: '个人微信 / 企业微信。装上微信插件后由它负责登录与收发消息，引擎提供模型与会话。',
    plugin: 'wechat-bot',
  },
  {
    id: 'bot:qq',
    name: 'QQ',
    icon: '🐧',
    desc: 'QQ 群聊 / 私聊机器人。装上 QQ 插件后由它负责登录与收发消息，引擎提供模型与会话。',
    plugin: 'qq-bot',
  },
]

function BotCard({ bot, installed }: { bot: BotChannel; installed: boolean }) {
  return (
    <div className={`channel-card ${installed ? 'connected' : 'disconnected'}`}>
      <div className="channel-icon">{bot.icon}</div>
      <div className="channel-info">
        <div className="channel-name">
          {bot.name}
          <span className="kb-count"> 机器人</span>
        </div>
        <div className="channel-desc" title={bot.desc}>
          {bot.desc}
        </div>
      </div>
      <div className="channel-status">
        <span className={`status-dot ${installed ? 'connected' : 'disconnected'}`} />
        <span className="status-text">{installed ? '插件已装' : '插件未接入'}</span>
      </div>
      <div className="channel-info" style={{ maxWidth: 180, fontSize: 11 }}>
        <div className="muted">插件：{bot.plugin}</div>
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
  const [installedPlugins, setInstalledPlugins] = useState<Set<string>>(new Set())
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
        setInstalledPlugins(new Set(skills.skills.map((s) => s.name)))
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
              : `${providers.filter((p) => p.hasKey).length} 个模型通道 · ${
                  BOT_CHANNELS.filter((b) => installedPlugins.has(b.plugin)).length
                }/${BOT_CHANNELS.length} 个机器人 · ${tools.length} 个工具通道`}
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
              <div className="set-title">机器人（微信 / QQ）</div>
              <div className="channels-list">
                {BOT_CHANNELS.map((b) => (
                  <BotCard key={b.id} bot={b} installed={installedPlugins.has(b.plugin)} />
                ))}
              </div>
              <div className="note note-info">
                机器人走插件：插件负责登录平台、收发消息，引擎这边只提供模型与会话。
                装上对应插件后这一项会自动变成「插件已装」——状态是照技能目录真实判断的，
                不是写死的开关。
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
