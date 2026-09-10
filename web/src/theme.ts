/**
 * 外观偏好的解释与应用。
 *
 * 偏好值存在 config 的 ``appearance.background``（后端有 schema 校验），这里
 * 只负责把那个字符串翻译成 CSS 变量。分成「规格 → 调色板 → DOM」两步是为了
 * 让解析逻辑可以脱离浏览器测试：真正容易出错的是解析，而不是赋值。
 *
 * 规格格式（与 config_builtins 的正则一一对应）:
 *   default          → 主题默认，清除所有覆盖
 *   preset:<name>    → 内置配色
 *   #rrggbb          → 纯色，浅/深配色按亮度自动挑
 *   url:https://…    → 封面图
 */

export interface Palette {
  bg: string
  panel: string
  border: string
  text: string
  muted: string
  accentSoft: string
  scheme: 'light' | 'dark'
  /** 背景图 URL；有值时 .app 让位给 body 显示图片 */
  image?: string
}

const LIGHT: Omit<Palette, 'bg'> & { bg: string } = {
  bg: '#fbfbfa',
  panel: '#ffffff',
  border: '#e6e4e0',
  text: '#1f1e1d',
  muted: '#8a8781',
  accentSoft: '#e8f1ec',
  scheme: 'light',
}

/** 深色配套。文字必须跟着变，否则深底上的深字直接不可读。 */
const DARK: Palette = {
  bg: '#14161a',
  panel: '#1c1f24',
  border: '#2a2e35',
  text: '#e8e6e2',
  muted: '#9aa0a6',
  accentSoft: '#22322a',
  scheme: 'dark',
}

export const PRESETS: Record<string, { label: string; palette: Palette }> = {
  paper: { label: '纸白', palette: { ...LIGHT, bg: '#fbfbfa' } },
  warm: { label: '暖沙', palette: { ...LIGHT, bg: '#f6f1e7', border: '#e8dfd0', panel: '#fffdf8' } },
  mint: { label: '薄荷', palette: { ...LIGHT, bg: '#eef4ef', border: '#dce8e0' } },
  sky: { label: '雾蓝', palette: { ...LIGHT, bg: '#eef2f8', border: '#dde5f0' } },
  ink: { label: '墨黑', palette: { ...DARK } },
  slate: { label: '石板', palette: { ...DARK, bg: '#1b1e24', panel: '#23272e', border: '#31363f' } },
}

/** 相对亮度（ITU-R BT.601）。用来决定自定义纯色配浅色还是深色文字。 */
function luminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return 0.299 * r + 0.587 * g + 0.114 * b
}

/**
 * 解析背景规格。返回 ``null`` 表示「没有覆盖」—— 调用方据此清除既有变量，
 * 而不是把默认值再写一遍（否则主题默认值改了这里会跟不上）。
 */
export function resolveBackground(spec: string | null | undefined): Palette | null {
  const s = (spec ?? '').trim()
  if (!s || s === 'default') return null

  if (s.startsWith('preset:')) {
    return PRESETS[s.slice('preset:'.length)]?.palette ?? null
  }
  if (/^#[0-9a-fA-F]{6}$/.test(s)) {
    const base = luminance(s) < 140 ? DARK : LIGHT
    return { ...base, bg: s }
  }
  if (s.startsWith('url:')) {
    const url = s.slice('url:'.length).trim()
    if (!url) return null
    // 图片模式下配色保持浅色：面板是不透明白卡，文字仍压在卡上。
    // 只把底色让出去，让 body 显示图片。
    return { ...LIGHT, image: url }
  }
  if (s.startsWith('file:')) {
    // 上传到本机的图片，由后端静态路由回显。走相对路径而不是绝对地址，
    // 这样换端口/换主机名打开也不会失效。
    const name = s.slice('file:'.length).trim()
    if (!name) return null
    return { ...LIGHT, image: `/api/appearance/background/${encodeURIComponent(name)}` }
  }
  return null
}

const OVERRIDE_KEYS = [
  '--bg',
  '--panel',
  '--border',
  '--text',
  '--muted',
  '--accent-soft',
  '--bg-image',
  '--app-bg',
  'color-scheme',
]

/** 把背景规格写到根元素上。传 ``default``/空值即恢复主题默认。 */
export function applyBackground(spec: string | null | undefined): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  for (const key of OVERRIDE_KEYS) root.style.removeProperty(key)

  const p = resolveBackground(spec)
  if (!p) return

  root.style.setProperty('--bg', p.bg)
  root.style.setProperty('--panel', p.panel)
  root.style.setProperty('--border', p.border)
  root.style.setProperty('--text', p.text)
  root.style.setProperty('--muted', p.muted)
  root.style.setProperty('--accent-soft', p.accentSoft)
  root.style.setProperty('color-scheme', p.scheme)
  if (p.image) {
    // .app 自带 background: var(--bg)，会盖住 body 的图；让位给它。
    root.style.setProperty('--bg-image', `url("${p.image}")`)
    root.style.setProperty('--app-bg', 'transparent')
  }
}

/** 从后端读回偏好并应用。失败时静默 —— 外观不该拦住启动。 */
export async function hydrateBackground(): Promise<void> {
  try {
    const res = await fetch('/api/config/appearance.background')
    if (!res.ok) return
    // GET /api/config/<path> returns the bare JSON value, not a {value} wrapper
    // (that shape belongs to PUT).
    const v = (await res.json()) as unknown
    applyBackground(typeof v === 'string' ? v : '')
  } catch {
    /* 忽略：保持主题默认 */
  }
}
