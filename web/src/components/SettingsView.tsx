import { useState } from 'react'
import { GROUPS, type SetPageId } from './settings/entries'
import {
  AgentConfigPage,
  IdentitiesPage,
  MemoryPage,
  ProvidersPage,
  SkillsPage,
  SoulPage,
  UsagePage,
} from './settings/agent'
import {
  AboutPage,
  AppearancePage,
  BackgroundPage,
  BackupPage,
  EnvVarsPage,
  LogsPage,
  McpPage,
  MountFoldersPage,
  PermissionsPage,
  StoragePage,
} from './settings/system'

/**
 * 设置 — mirrors the Android SettingsScreen grouping.
 *
 * 主页是一棵与 Kotlin 同构的分组树(iOS/Android 设置样式:分组卡片 + 每行
 * 图标圆底 / 标题 / 副标题 / chevron)。点进任意一行后在内容区内打开对应的
 * detail 页(顶栏有「‹ 设置」返回),不会产生第二列导航。可落地的子页全部
 * 走真实后端:身份/模型走 /api/settings,外观/沙箱/环境变量走 /api/config,
 * 记忆/日志/存储/备份走 /api/system。
 */
export function SettingsView() {
  const [page, setPage] = useState<SetPageId | null>(null)

  const go = (id: SetPageId) => setPage(id)
  const onBack = () => setPage(null)

  if (page === 'providers') return <ProvidersPage onBack={onBack} />
  if (page === 'identities') return <IdentitiesPage onBack={onBack} />
  if (page === 'usage') return <UsagePage onBack={onBack} />
  if (page === 'agent') return <AgentConfigPage onBack={onBack} />
  if (page === 'appearance') return <AppearancePage onBack={onBack} />
  if (page === 'soul') return <SoulPage onBack={onBack} go={go} />
  if (page === 'skills') return <SkillsPage onBack={onBack} go={go} />
  if (page === 'memory') return <MemoryPage onBack={onBack} />
  if (page === 'mcp') return <McpPage onBack={onBack} />
  if (page === 'env') return <EnvVarsPage onBack={onBack} />
  if (page === 'storage') return <StoragePage onBack={onBack} />
  if (page === 'mount') return <MountFoldersPage onBack={onBack} />
  if (page === 'backup') return <BackupPage onBack={onBack} />
  if (page === 'perms') return <PermissionsPage onBack={onBack} />
  if (page === 'background') return <BackgroundPage onBack={onBack} />
  if (page === 'logs') return <LogsPage onBack={onBack} />
  if (page === 'about') return <AboutPage onBack={onBack} />

  const openItem = (id: string) => {
    for (const g of GROUPS) {
      const item = g.items.find((it) => it.id === id)
      if (item) {
        if (item.kind === 'link' && item.href) {
          window.open(item.href, '_blank', 'noopener')
        } else {
          setPage(id as SetPageId)
        }
        return
      }
    }
  }

  return (
    <div className="view settings">
      <div className="settings-detail">
        <div className="toolbar">
          <h2>设置</h2>
          <span className="muted">与 Android 设置同构的分组 · 各子页即时保存</span>
        </div>

        {GROUPS.map((group) => (
          <div key={group.id} className="set-group">
            <h4>{group.title}</h4>
            <div className="set-card">
              {group.items.map((item, i) => (
                <div key={item.id}>
                  <button className="set-item" onClick={() => openItem(item.id)}>
                    <span className="set-ic" style={{ background: item.tint }}>
                      {item.icon}
                    </span>
                    <span className="set-text">
                      <span className="set-title">{item.title}</span>
                      <span className="muted set-sub">{item.subtitle}</span>
                    </span>
                    <span className="set-chev">›</span>
                  </button>
                  {i < group.items.length - 1 && <div className="set-divider" />}
                </div>
              ))}
            </div>
            {group.footer && <p className="muted set-footer">{group.footer}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}

export default SettingsView
