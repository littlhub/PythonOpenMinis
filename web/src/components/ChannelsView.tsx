import { useState } from 'react'

/**
 * ChannelsView - 通道管理页面
 * 管理 AI Agent 的连接通道，如 Discord、WhatsApp、企微等
 */
export function ChannelsView() {
  const [channels] = useState([
    { id: 'discord', name: 'Discord', icon: '💬', status: 'connected', description: 'Discord 机器人集成' },
    { id: 'whatsapp', name: 'WhatsApp', icon: '📱', status: 'disconnected', description: 'WhatsApp Business API' },
    { id: 'wechat', name: '企业微信', icon: '💼', status: 'connected', description: '企微消息和审批通知' },
    { id: 'dingtalk', name: '钉钉', icon: '🔔', status: 'disconnected', description: '钉钉机器人和水印' },
    { id: 'telegram', name: 'Telegram', icon: '✈️', status: 'disconnected', description: 'Telegram Bot API' },
    { id: 'email', name: 'Email', icon: '📧', status: 'connected', description: '邮件接收和发送' },
  ])

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>📡 通道管理</h2>
          <span className="kb-count">{channels.filter((c) => c.status === 'connected').length} 个已连接</span>
        </div>

        <p className="channels-desc">
          管理 AI Agent 的连接通道。通过配置不同的消息平台，让助手能够跨平台响应你的需求。
        </p>

        {/* 通道列表 */}
        <div className="channels-list">
          {channels.map((ch) => (
            <div key={ch.id} className={`channel-card ${ch.status}`}>
              <div className="channel-icon">{ch.icon}</div>
              <div className="channel-info">
                <div className="channel-name">{ch.name}</div>
                <div className="channel-desc">{ch.description}</div>
              </div>
              <div className="channel-status">
                <span className={`status-dot ${ch.status}`} />
                <span className="status-text">
                  {ch.status === 'connected' ? '已连接' : '未连接'}
                </span>
              </div>
              <button className="channel-btn">
                {ch.status === 'connected' ? '配置' : '连接'}
              </button>
            </div>
          ))}
        </div>

        {/* 添加通道 */}
        <button className="memory-add-btn">
          ＋ 添加新通道
        </button>
      </div>
    </div>
  )
}
