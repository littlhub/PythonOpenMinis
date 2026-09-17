import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { PluginCard } from './PluginCard'
import type { PluginStatus } from '../types'

type Filter = 'all' | 'installed' | 'channel' | 'other'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'installed', label: '已安装' },
  { id: 'channel', label: '通道' },
  { id: 'other', label: '其他' },
]

/**
 * PluginsView —— 插件管理页。
 *
 * 与「通道」页分工：
 * - 通道页只管 `category=channel` 的那批（在「通道」处装入插件、把 QQ 机器人调通）；
 * - 这里管全部：内置插件的安装、**导入第三方插件包**（zip / 本机目录，没有
 *   plugin.json 也能装 —— 引擎会按 package.json / 入口文件推断，见后端
 *   `plugins.store.infer_manifest`）、外部程序插件的启停与日志。
 */
export function PluginsView() {
  const [plugins, setPlugins] = useState<PluginStatus[]>([])
  const [dir, setDir] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeBad, setNoticeBad] = useState(false)
  const [importPath, setImportPath] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const load = useCallback(async () => {
    setError('')
    try {
      const data = await api.pluginsList()
      setPlugins(data.plugins)
      setDir(data.dir)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const say = (message: string, bad = false) => {
    setNotice(message)
    setNoticeBad(bad)
  }

  const doImport = async () => {
    const path = importPath.trim()
    if (!path) return
    setBusy(true)
    setNotice('')
    try {
      const r = await api.pluginImport(path)
      say(`已导入插件「${r.plugin.name || r.id}」`)
      setImportPath('')
      await load()
    } catch (e) {
      say(e instanceof Error ? e.message : String(e), true)
    } finally {
      setBusy(false)
    }
  }

  const doUpload = async (file: File) => {
    setBusy(true)
    setNotice('')
    try {
      const r = await api.pluginUpload(file)
      say(`已导入插件「${r.plugin.name || r.id}」`)
      await load()
    } catch (e) {
      say(e instanceof Error ? e.message : String(e), true)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const shown = useMemo(() => {
    if (filter === 'all') return plugins
    if (filter === 'installed') return plugins.filter((p) => p.installed)
    if (filter === 'channel') return plugins.filter((p) => p.category === 'channel')
    return plugins.filter((p) => p.category !== 'channel')
  }, [plugins, filter])

  const runningCount = plugins.filter((p) => p.running).length
  const installedCount = plugins.filter((p) => p.installed).length

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>🧩 插件</h2>
          <span className="kb-count">
            {loading
              ? '读取中…'
              : `${installedCount} 个已安装 · ${runningCount} 个在跑 · 共 ${plugins.length} 个`}
          </span>
        </div>

        {error && <div className="note note-warn">加载失败：{error}</div>}

        <p className="channels-desc">
          插件 = 给引擎外接能力的包。三种形态：<b>引擎内驱动</b>（如 QQ 通道，
          引擎自己连平台）、<b>外部程序</b>（起 node / python 子进程，dsh-bridge
          这类现成项目走这条）、<b>仅登记</b>（暂时没识别出入口，能看到能删）。
          插件目录：<span className="mono">{dir}</span>
        </p>

        <div className="set-section" style={{ marginTop: 8 }}>
          <div className="set-title">导入插件包</div>
          <div className="plugin-import">
            <input
              placeholder="本机路径，例如 C:\downloads\dsh-bridge-main.zip（或解压后的目录）"
              value={importPath}
              onChange={(e) => setImportPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void doImport()
              }}
            />
            <button
              className="plugin-btn primary"
              disabled={busy || !importPath.trim()}
              onClick={() => void doImport()}
            >
              {busy ? '导入中…' : '导入'}
            </button>
            <button
              className="plugin-btn"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              选择 zip…
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".zip"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void doUpload(f)
              }}
            />
          </div>
          <div className="note note-info">
            没有 <span className="mono">plugin.json</span> 的包也能装：引擎会看{' '}
            <span className="mono">package.json</span> / 入口文件推断出启动命令并生成一份清单，
            之后你在下面「配置」里可以手动改。<b>插件包里的代码不会被自动执行</b>，
            只有你点了「启动」才会跑。
          </div>
        </div>

        {notice && (
          <div className={`note ${noticeBad ? 'note-warn' : 'note-ok'}`}>{notice}</div>
        )}

        <div className="kb-categories">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              className={`kb-cat-btn ${filter === f.id ? 'active' : ''}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="kb-empty">读取插件目录…</div>
        ) : shown.length === 0 ? (
          <div className="kb-empty">
            这个分类下还没有插件。内置的 QQ 通道可以在「管理 · 通道」里一键安装，
            或者在上面导入一个插件包。
          </div>
        ) : (
          <div className="plugin-list">
            {shown.map((p) => (
              <PluginCard
                key={p.id}
                plugin={p}
                onChanged={async () => {
                  await load()
                }}
                onNotice={say}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
