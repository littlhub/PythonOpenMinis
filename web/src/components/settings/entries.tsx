/** The settings home tree — mirrors the Android SettingsScreen grouping
 * (LLM providers → appearance → agent runtime → storage → permissions →
 * background → logs → about). */

export type SetPageId =
  | 'providers'
  | 'identities'
  | 'usage'
  | 'appearance'
  | 'soul'
  | 'skills'
  | 'memory'
  | 'mcp'
  | 'env'
  | 'storage'
  | 'mount'
  | 'backup'
  | 'perms'
  | 'background'
  | 'logs'
  | 'about'

export interface SettingsItemDef {
  id: string
  title: string
  subtitle: string
  icon: string // emoji glyph
  tint: string // roundel background
  kind: 'page' | 'link'
  href?: string
}

export interface SettingsGroupDef {
  id: string
  title: string
  footer?: string
  items: SettingsItemDef[]
}

export const BLUE = '#0a66ff'
export const PURPLE = '#5856d6'
export const ORANGE = '#ff9500'
export const TEAL = '#30b0c7'
export const GREEN = '#1f9d55'

export const GROUPS: SettingsGroupDef[] = [
  {
    id: 'llm',
    title: '模型与智能体',
    items: [
      {
        id: 'providers',
        title: '模型服务',
        subtitle: '厂商 API Key · Base URL · 默认模型 · 设为当前',
        icon: '🔌',
        tint: BLUE,
        kind: 'page',
      },
      {
        id: 'identities',
        title: '身份与人格',
        subtitle: '角色人设 · 可用技能/工具 · 新建自定义身份',
        icon: '🧑‍🚀',
        tint: PURPLE,
        kind: 'page',
      },
      {
        id: 'usage',
        title: 'Token 用量',
        subtitle: '会话内计数与历史统计',
        icon: '📊',
        tint: BLUE,
        kind: 'page',
      },
    ],
  },
  {
    id: 'appearance',
    title: '外观',
    items: [
      {
        id: 'appearance',
        title: '外观与主题',
        subtitle: '主题 · 字号 · 减少动效',
        icon: '🎨',
        tint: PURPLE,
        kind: 'page',
      },
    ],
  },
  {
    id: 'runtime',
    title: 'Agent 运行时',
    items: [
      {
        id: 'soul',
        title: '灵魂 (Soul)',
        subtitle: '人格设定与个性化记忆 — 由当前身份的 persona 驱动',
        icon: '✨',
        tint: ORANGE,
        kind: 'page',
      },
      {
        id: 'skills',
        title: '技能与工具',
        subtitle: '已移植工具目录(Shell / 文件 / 视觉 / 记忆 / 浏览器)',
        icon: '🧩',
        tint: BLUE,
        kind: 'page',
      },
      {
        id: 'memory',
        title: '记忆 (Memory)',
        subtitle: '查看/编辑每日日志与 GLOBAL.md 长期记忆',
        icon: '🧠',
        tint: PURPLE,
        kind: 'page',
      },
      {
        id: 'mcp',
        title: 'MCP 集成',
        subtitle: '外部 MCP 服务器接入',
        icon: '🀄',
        tint: TEAL,
        kind: 'page',
      },
      {
        id: 'env',
        title: '环境变量',
        subtitle: '注入沙箱命令进程的环境变量(KV)',
        icon: '💻',
        tint: GREEN,
        kind: 'page',
      },
    ],
  },
  {
    id: 'storage',
    title: '存储',
    items: [
      {
        id: 'storage',
        title: '存储管理',
        subtitle: '数据目录占用一览',
        icon: '📦',
        tint: BLUE,
        kind: 'page',
      },
      {
        id: 'mount',
        title: '挂载的文件夹',
        subtitle: '工作空间绑定的电脑目录(沙箱根)',
        icon: '📁',
        tint: GREEN,
        kind: 'page',
      },
      {
        id: 'backup',
        title: '备份与恢复',
        subtitle: '导出设置与记忆为 zip',
        icon: '🗄️',
        tint: GREEN,
        kind: 'page',
      },
    ],
  },
  {
    id: 'perms',
    title: '权限',
    items: [
      {
        id: 'perms',
        title: '系统能力与权限',
        subtitle: '沙箱开关 · 命令超时 · 数据访问',
        icon: '🛡️',
        tint: BLUE,
        kind: 'page',
      },
    ],
  },
  {
    id: 'background',
    title: '后台与通知',
    items: [
      {
        id: 'background',
        title: '后台运行与服务',
        subtitle: '服务器地址 · 常驻运行说明',
        icon: '🔋',
        tint: ORANGE,
        kind: 'page',
      },
    ],
  },
  {
    id: 'logs',
    title: '日志',
    items: [
      {
        id: 'logs',
        title: '日志管理',
        subtitle: '查看/下载运行日志',
        icon: '📄',
        tint: BLUE,
        kind: 'page',
      },
    ],
  },
  {
    id: 'about',
    title: '关于',
    items: [
      {
        id: 'about',
        title: '关于 OpenMinis',
        subtitle: '版本 · 数据目录 · 开源信息',
        icon: 'ℹ️',
        tint: BLUE,
        kind: 'page',
      },
      {
        id: 'privacy',
        title: '隐私政策',
        subtitle: 'openminis.github.io',
        icon: '🖐️',
        tint: BLUE,
        kind: 'link',
        href: 'https://openminis.github.io/privacy-policy.html',
      },
      {
        id: 'feedback',
        title: '反馈',
        subtitle: 'GitHub Issues · 邮件',
        icon: '💬',
        tint: BLUE,
        kind: 'link',
        href: 'https://github.com/OpenMinis/OpenMinis/issues/new',
      },
    ],
  },
]
