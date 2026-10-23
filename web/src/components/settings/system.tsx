/** Settings detail pages for the system side of the tree:
 * 外观 / 环境变量 / 权限 / 后台 / 存储 / 挂载目录 / 备份 / 日志 / 关于 / MCP.
 * Scalar config rows save immediately through /api/config (same validation the
 * CLI uses); system pages read the read-mostly /api/system endpoints.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, downloadUrl } from '../../api'
import { PRESETS, applyBackground } from '../../theme'
import type { ConfigFieldInfo, WorkspaceInfo } from '../../types'
import {
  DetailShell,
  KvFacts,
  MiniBar,
  Note,
  SectionCard,
  Spinner,
  StatusLine,
  fmtBytes,
  fmtTime,
  useAsync,
} from './ui'

// ---------------------------------------------------------------------------
// immediate-save scalar config topic editor
// ---------------------------------------------------------------------------
function useConfigTopic(topic: string) {
  const [fields, setFields] = useState<ConfigFieldInfo[] | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const fs = await api.configList(topic)
      setFields(fs)
      const v: Record<string, unknown> = {}
      for (const f of fs) v[f.path] = f.value
      setValues(v)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [topic])

  useEffect(() => {
    void load()
  }, [load])

  // Returns whether the write landed. Callers that apply a side effect (the
  // background picker repaints the shell) must not do so on a rejected value —
  // the backend schema is the authority on what a legal spec looks like.
  const setField = useCallback(
    async (path: string, value: unknown): Promise<boolean> => {
      const prev = values[path]
      setValues((v) => ({ ...v, [path]: value }))
      setError(null)
      setSaved(null)
      try {
        const r = await api.configSet(path, value)
        // 服务器可能改写了值（打码的 Key 存回来就是「没改」）——以它为准，
        // 否则输入框会一直显示「••• (hidden)」而用户以为改成功了。
        setValues((v) => ({ ...v, [path]: r.newValue }))
        setSaved(`已保存 ${path} ✓`)
        setTimeout(() => setSaved(null), 2200)
        return true
      } catch (e) {
        setValues((v) => ({ ...v, [path]: prev }))
        setError((e as Error).message)
        return false
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [values],
  )

  return { fields, values, error, saved, setField, reload: load }
}

function Row(props: {
  label: string
  desc?: string
  children: ReactNode
}) {
  return (
    <div className="cfg-row">
      <div className="cfg-text">
        <span className="cfg-label">{props.label}</span>
        {props.desc && <span className="muted cfg-desc">{props.desc}</span>}
      </div>
      <div className="cfg-control">{props.children}</div>
    </div>
  )
}

const THEME_OPTIONS = [
  ['system', '跟随系统'],
  ['light', '浅色'],
  ['dark', '深色'],
]

// ---------------------------------------------------------------------------
// 外观与主题
// ---------------------------------------------------------------------------
export function AppearancePage(props: { onBack: () => void }) {
  const { fields, values, error, saved, setField } = useConfigTopic('appearance')
  // 图片 URL 输入框是本地草稿：只有点了「应用」或回车才写盘，
  // 否则每敲一个字符都会触发一次 config 校验。
  const [bgUrl, setBgUrl] = useState('')
  const [bgUrlTouched, setBgUrlTouched] = useState(false)

  const spec = String(values['appearance.background'] ?? 'default')
  const currentUrl = spec.startsWith('url:') ? spec.slice(4) : ''

  useEffect(() => {
    if (!bgUrlTouched) setBgUrl(currentUrl)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUrl, bgUrlTouched])

  const pickBackground = async (v: string) => {
    if (await setField('appearance.background', v)) applyBackground(v)
  }

  const bgFileRef = useRef<HTMLInputElement>(null)
  const [bgUploading, setBgUploading] = useState(false)
  const [bgUploadErr, setBgUploadErr] = useState<string | null>(null)

  const uploadLocalBackground = async (file: File) => {
    setBgUploading(true)
    setBgUploadErr(null)
    try {
      // 先落盘再写 config：图片没存进去就把配置指向它，会得到一个打不开的
      // 背景，而且用户看不出是哪一步失败了。
      const r = await api.appearanceUploadBackground(file)
      await pickBackground(r.spec)
    } catch (e) {
      setBgUploadErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBgUploading(false)
    }
  }

  if (!fields) return <DetailShell title="外观与主题" onBack={props.onBack}><Spinner /></DetailShell>
  return (
    <DetailShell
      title="外观与主题"
      subtitle="对应 Android 的 AppearanceScreen。偏好经同一套 config 校验后落盘。"
      onBack={props.onBack}
      extra={<StatusLine ok={saved} err={error} />}
    >
      <SectionCard>
        {fields.map((f) => {
          const value = values[f.path]
          if (f.path === 'appearance.theme') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <select value={String(value ?? 'system')} onChange={(e) => void setField(f.path, e.target.value)}>
                  {THEME_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Row>
            )
          }
          if (f.path === 'appearance.fontScale') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <div className="range-wrap">
                  <input
                    type="range" min={0.5} max={2.5} step={0.1}
                    value={Number(value ?? 1)}
                    onChange={(e) => void setField(f.path, Number(e.target.value))}
                  />
                  <code>{Number(value ?? 1).toFixed(1)}×</code>
                </div>
              </Row>
            )
          }
          if (f.path === 'appearance.reduceMotion') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <input type="checkbox" checked={!!value} onChange={(e) => void setField(f.path, e.target.checked)} />
              </Row>
            )
          }
          if (f.path === 'appearance.background') {
            const isCustomHex = /^#[0-9a-fA-F]{6}$/.test(spec)
            const hex = isCustomHex ? spec : '#fbfbfa'
            return (
              <Row key={f.path} label="背景" desc="主区底色。选预设、自定颜色,或填一张图片 URL。">
                <div className="bg-picker">
                  <div className="bg-swatches">
                    {Object.entries(PRESETS).map(([key, p]) => (
                      <button
                        key={key}
                        type="button"
                        title={p.label}
                        className={`bg-swatch ${spec === `preset:${key}` ? 'on' : ''}`}
                        style={{
                          background: p.palette.bg,
                          borderColor: p.palette.border,
                          color: p.palette.text,
                        }}
                        onClick={() => void pickBackground(`preset:${key}`)}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>

                  <div className="bg-custom">
                    <span className="muted">自定义颜色</span>
                    <input
                      type="color"
                      value={hex}
                      onChange={(e) => void pickBackground(e.target.value)}
                    />
                    {isCustomHex && <code>{spec}</code>}
                  </div>

                  <div className="bg-custom">
                    <span className="muted">本地图片</span>
                    <input
                      ref={bgFileRef}
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      style={{ display: 'none' }}
                      onChange={(e) => {
                        const f = e.target.files?.[0]
                        // 清空 value：否则连续选同一个文件不会再触发 change
                        e.target.value = ''
                        if (f) void uploadLocalBackground(f)
                      }}
                    />
                    <button
                      className="btn-create"
                      disabled={bgUploading}
                      onClick={() => bgFileRef.current?.click()}
                    >
                      {bgUploading ? '上传中…' : '选择图片'}
                    </button>
                    <span className="muted">png / jpg / webp / gif，≤ 8MB</span>
                    {bgUploadErr && <span className="error">{bgUploadErr}</span>}
                  </div>

                  <div className="bg-custom">
                    <span className="muted">图片 URL</span>
                    <input
                      type="text"
                      placeholder="https://example.com/bg.jpg"
                      value={bgUrl}
                      onChange={(e) => {
                        setBgUrlTouched(true)
                        setBgUrl(e.target.value)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && bgUrl.trim()) {
                          void pickBackground(`url:${bgUrl.trim()}`)
                        }
                      }}
                    />
                    <button
                      className="btn-create"
                      disabled={!bgUrl.trim() || bgUrl.trim() === currentUrl}
                      onClick={() => void pickBackground(`url:${bgUrl.trim()}`)}
                    >
                      应用
                    </button>
                  </div>

                  <div className="bg-custom">
                    <span className="muted">
                      {spec === 'default'
                        ? '当前:主题默认'
                        : spec.startsWith('url:')
                          ? '当前:网络图片'
                          : spec.startsWith('file:')
                            ? '当前:本地图片'
                            : `当前:${spec}`}
                    </span>
                    {spec !== 'default' && (
                      <button
                        className="link"
                        onClick={() => {
                          setBgUrlTouched(false)
                          void pickBackground('default')
                        }}
                      >
                        恢复默认
                      </button>
                    )}
                  </div>
                </div>
              </Row>
            )
          }
          return null
        })}
        <Note kind="info">
          Web 界面主题跟随浏览器/系统外观;此处的 theme 偏好主要作用于偏好持久化与后续客户端。
          背景则立即生效,并随 config 落盘(换浏览器打开一致)。
        </Note>
      </SectionCard>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 环境变量 — sandbox.envExtra (merged into every sandbox shell env)
// ---------------------------------------------------------------------------
export function EnvVarsPage(props: { onBack: () => void }) {
  const [rows, setRows] = useState<[string, string][]>([])
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    api
      .configGet('sandbox.envExtra')
      .then((raw) => {
        let obj: unknown = {}
        if (typeof raw === 'string' && raw.trim()) {
          try {
            obj = JSON.parse(raw)
          } catch {
            obj = {}
          }
        } else if (raw && typeof raw === 'object') {
          obj = raw
        }
        const entries = (obj && typeof obj === 'object' ? Object.entries(obj as Record<string, unknown>) : [])
          .filter(([, v]) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')
          .map(([k, v]) => [k, String(v)] as [string, string])
        setRows(entries.length ? entries : [['', '']])
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoaded(true))
  }, [])

  const patch = (i: number, field: 0 | 1, v: string) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? (field === 0 ? [v, r[1]] : [r[0], v]) : r)))

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(null)
    const obj: Record<string, string> = {}
    for (const [k, v] of rows) {
      if (!k.trim()) continue
      obj[k.trim()] = v
    }
    try {
      await api.configSet('sandbox.envExtra', JSON.stringify(obj, null, 2))
      setRows(Object.entries(obj).length ? Object.entries(obj) : [['', '']])
      setSaved('已保存 ✓')
      setTimeout(() => setSaved(null), 2200)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) return <DetailShell title="环境变量" onBack={props.onBack}><Spinner /></DetailShell>
  return (
    <DetailShell
      title="环境变量"
      subtitle="对应 Android 的 EnvVarsCollection:键值会合并进沙箱命令进程的环境(sandbox.envExtra),新会话的命令立即可见。"
      onBack={props.onBack}
      extra={
        <>
          <StatusLine ok={saved} err={error} />
          <button onClick={() => void save()} disabled={saving}>{saving ? 'Saving…' : '保存'}</button>
        </>
      }
    >
      <SectionCard title="沙箱环境变量" hint="留空值则忽略该行;值为字符串。">
        {rows.map(([k, v], i) => (
          <EnvRow key={i} k={k} v={v} idx={i} patch={patch} remove={() => setRows((prev) => prev.filter((_, idx) => idx !== i))} />
        ))}
        <div className="row-actions">
          <button className="btn-soft" onClick={() => setRows((prev) => [...prev, ['', '']])}>＋ 添加变量</button>
        </div>
      </SectionCard>
      <Note kind="warn">
        例如 <code>{'{"LANG": "zh_CN.UTF-8"}'}</code>、<code>{'{"HTTPS_PROXY": "…"}'}</code>。
        敏感值请勿写入并随备份导出——本地明文保存。
      </Note>
    </DetailShell>
  )
}

// 环境变量行：值默认密文显示（API Key 等敏感值不明文），可点「显示」切换
function EnvRow(props: {
  k: string
  v: string
  idx: number
  patch: (i: number, slot: 0 | 1, value: string) => void
  remove: () => void
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="env-row">
      <input placeholder="KEY" className="mono" value={props.k} onChange={(e) => props.patch(props.idx, 0, e.target.value)} />
      <input
        placeholder="value"
        className="mono"
        type={show ? 'text' : 'password'}
        autoComplete="off"
        value={props.v}
        onChange={(e) => props.patch(props.idx, 1, e.target.value)}
      />
      <button className="link" onClick={() => setShow((s) => !s)}>{show ? '隐藏' : '显示'}</button>
      <button className="link danger" onClick={props.remove}>移除</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 系统能力与权限 (对应 Android 的 SystemPermissionsScreen;桌面版为能力清单)
// ---------------------------------------------------------------------------
export function PermissionsPage(props: { onBack: () => void }) {
  const { fields, values, error, saved, setField } = useConfigTopic('sandbox')
  if (!fields) return <DetailShell title="系统能力与权限" onBack={props.onBack}><Spinner /></DetailShell>
  return (
    <DetailShell
      title="系统能力与权限"
      subtitle="Android 的系统权限页(无障碍/Shizuku…)在桌面版没有对应概念。这里列出桌面 Agent 实际持有的能力与沙箱开关。"
      onBack={props.onBack}
      extra={<StatusLine ok={saved} err={error} />}
    >
      <SectionCard title="已授予的能力(桌面版)" hint="运行于你的电脑用户会话,以下能力对沙箱命令开放">
        <Row label="读写工作区文件" desc="file_read / file_write / file_edit 作用于当前会话的工作空间">
          <span className="pill ok">已授予</span>
        </Row>
        <Row label="执行命令" desc="shell_execute 经持久化 shell 运行,受超时与清洗保护">
          <span className="pill ok">已授予</span>
        </Row>
        <Row label="读取图片 / 使用浏览器" desc="read_image 缩放后送入模型;browser_use 执行器待移植">
          <span className="pill warn">部分</span>
        </Row>
        <Row label="写长期记忆" desc="memory_write 落盘到 memory/YYYY-MM-DD.md">
          <span className="pill ok">已授予</span>
        </Row>
      </SectionCard>

      <SectionCard title="沙箱配置">
        {fields.map((f) => {
          if (f.path === 'sandbox.envExtra') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <span className="muted">(在「环境变量」页以 KV 编辑)</span>
              </Row>
            )
          }
          if (f.path === 'sandbox.enabled') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <input type="checkbox" checked={!!values[f.path]} onChange={(e) => void setField(f.path, e.target.checked)} />
              </Row>
            )
          }
          if (f.path === 'sandbox.shellTimeoutSeconds') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <input
                  type="number" min={1} max={3600}
                  value={Number(values[f.path] ?? 120)}
                  onChange={(e) => void setField(f.path, Number(e.target.value))}
                />
              </Row>
            )
          }
          return null
        })}
      </SectionCard>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 后台运行与服务
// ---------------------------------------------------------------------------
export function BackgroundPage(props: { onBack: () => void }) {
  const { fields, values, error, saved, setField } = useConfigTopic('server')
  // 端口是自由输入,不能每敲一位就写盘 —— 输 "8" 是合法端口,用户的 8765 会在
  // 敲到第三位之前就被改成 8。所以先攒在本地,回车或失焦时才提交。
  const [draftPort, setDraftPort] = useState<string | null>(null)

  const savedHost = String(values['server.host'] ?? '127.0.0.1')
  const savedPort = String(values['server.port'] ?? '8765')
  const shownPort = draftPort ?? savedPort
  // 「仅本机」= 回环地址;其余(0.0.0.0 / 局域网 IP)一律按对外监听展示。
  const loopback = ['127.0.0.1', 'localhost', '::1', ''].includes(savedHost)
  const dirty = draftPort !== null && draftPort !== savedPort

  const commitPort = async () => {
    if (draftPort === null) return
    const n = Number(draftPort)
    if (!Number.isInteger(n) || n < 1 || n > 65535) {
      setDraftPort(null) // 非法输入直接退回已保存值,端口范围以后端 schema 为准
      return
    }
    await setField('server.port', n)
    setDraftPort(null)
  }

  return (
    <DetailShell
      title="后台运行与服务"
      subtitle="对应 Android 的 Background & Notifications(电池优化/自启动引导)——桌面进程常驻,不适用;此处为服务配置。"
      onBack={props.onBack}
    >
      {!fields ? (
        <Spinner />
      ) : (
        <SectionCard title="本地服务" hint={`当前访问地址 ${window.location.origin}`}>
          <Row label="监听端口" desc="后端 FastAPI 服务端口。默认 8765,范围 1–65535。">
            <input
              type="number"
              min={1}
              max={65535}
              value={shownPort}
              onChange={(e) => setDraftPort(e.target.value)}
              onBlur={() => void commitPort()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void commitPort()
              }}
              style={{ width: 96 }}
            />
            {dirty && <span className="muted">回车保存</span>}
          </Row>
          <Row
            label="允许局域网访问"
            desc="开启后监听 0.0.0.0,同一网络下的其他设备可用本机 IP 访问;关闭则只允许本机。"
          >
            <input
              type="checkbox"
              checked={!loopback}
              onChange={(e) =>
                void setField('server.host', e.target.checked ? '0.0.0.0' : '127.0.0.1')
              }
            />
          </Row>
          {error && <p className="error">{error}</p>}
          {saved && <p className="muted">{saved}</p>}
        </SectionCard>
      )}
      <Note kind="warn">
        端口与监听地址改动后需 <strong>重启服务</strong> 才生效（命令行 <code>--port</code> /{' '}
        <code>--host</code> 优先于此处设置）。换端口后请用新地址访问本页。
      </Note>
      <Note kind="info">
        网页由本地 FastAPI 服务(run.bat / OpenMinis.exe)提供。关闭浏览器不会中断对话;
        保持服务运行即可在后台接收任务。
      </Note>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 存储管理
// ---------------------------------------------------------------------------
export function StoragePage(props: { onBack: () => void }) {
  const { data, error, loading } = useAsync(() => api.systemStorage())
  return (
    <DetailShell title="存储管理" subtitle="数据目录占用一览(对应 Android 的 StorageManagementScreen)。" onBack={props.onBack}>
      {loading ? <Spinner /> : error ? <p className="error">{error}</p> : data && (
        <>
          <SectionCard title={`总占用 ${fmtBytes(data.totalBytes)}`} hint={data.root}>
            {data.items.map((it) => (
              <MiniBar
                key={it.path}
                label={it.name}
                bytes={it.bytes}
                frac={data.totalBytes ? it.bytes / data.totalBytes : 0}
                sub={`${it.fileCount} 个文件`}
              />
            ))}
          </SectionCard>
          <p className="muted">
            数据全部保存在本机数据目录;删除会话/工作空间可在左侧空间树操作,记忆文件请到「记忆」页管理。
          </p>
        </>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 挂载的文件夹 — 工作空间绑定的电脑目录
// ---------------------------------------------------------------------------
export function MountFoldersPage(props: { onBack: () => void }) {
  const ws = useAsync<WorkspaceInfo[]>(async () => (await api.workspaces()).workspaces)
  const info = useAsync(() => api.appinfo())
  return (
    <DetailShell
      title="挂载的文件夹"
      subtitle="对应 Android 的 MountedFolders / SharedFolders:把电脑里的目录交给会话作沙箱根。桌面版以「工作空间」表达——新建/编辑空间时可选择电脑目录。"
      onBack={props.onBack}
    >
      {ws.loading || info.loading ? <Spinner /> : ws.error ? <p className="error">{ws.error}</p> : (
        <SectionCard title="工作空间与绑定目录" hint="绑定了 path 的空间,其会话的沙箱 shell 会以该目录为根。">
          {!ws.data?.length && <p className="empty">暂无工作空间。</p>}
          {(ws.data ?? []).map((w) => (
            <div key={w.id} className="skill-row">
              <span className="skill-dot on" />
              <div className="skill-text">
                <strong>{w.name}</strong>
                <span className="muted mono">{w.path || '(未绑定,使用默认目录)'}</span>
              </div>
              <code>{w.sessionCount} 会话</code>
            </div>
          ))}
        </SectionCard>
      )}
      {info.data && (
        <Note kind="info">
          默认工作目录(未绑定的会话):<code>{info.data.workspaceDir}</code>
        </Note>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 备份与恢复
// ---------------------------------------------------------------------------
export function BackupPage(props: { onBack: () => void }) {
  return (
    <DetailShell
      title="备份与恢复"
      subtitle="对应 Android 的 Backup & Restore。导出内容:settings.json(含厂商配置) + 偏好 + 全部记忆文件。"
      onBack={props.onBack}
    >
      <SectionCard title="导出备份">
        <p className="muted">点击后浏览器会下载一个 openminis-backup-&lt;时间&gt;.zip。</p>
        <div className="row-actions">
          <button onClick={() => downloadUrl('/system/backup/download')}>导出备份 (zip)</button>
        </div>
      </SectionCard>
      <Note kind="warn">
        恢复导入尚未移植(对应 BackupImporter)。导出包包含 API Key 等明文配置,请妥善保管,不要分享给他人。
      </Note>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 日志管理
// ---------------------------------------------------------------------------
export function LogsPage(props: { onBack: () => void }) {
  const { data, error, loading, reload } = useAsync(() => api.systemLogs(800))
  return (
    <DetailShell
      title="日志管理"
      subtitle="运行日志(旋转文件,最多 8 MB × 4)。排障时把最后几行发给开发者即可。"
      onBack={props.onBack}
      extra={
        <div className="row-actions">
          <button className="btn-soft" onClick={reload} disabled={loading}>刷新</button>
          <button onClick={() => downloadUrl('/system/logs/download')}>下载</button>
        </div>
      }
    >
      {loading ? <Spinner /> : error ? <p className="error">{error}</p> : data && (
        <>
          <p className="muted pathline">
            {data.path} · {fmtBytes(data.size)} · 更新于 {fmtTime(data.updatedAt)}
            {data.rotated.length > 0 && ` · 轮转文件: ${data.rotated.join(', ')}`}
          </p>
          {data.note ? (
            <Note kind="info">{data.note}</Note>
          ) : (
            <pre className="terminal log-view">{data.lines.join('\n')}</pre>
          )}
        </>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// MCP 集成
// ---------------------------------------------------------------------------
// 语音识别 — 本地离线 STT（speech.* 主题）
// ---------------------------------------------------------------------------
// PORT: 没有 Kotlin 页面对应物。Android 走系统 SpeechRecognizer，没有可调项；
// 桌面版换成双引擎（本地 sherpa-onnx / 模型 API），才第一次有了这些旋钮。
// 「本地 / API」可随时互换：本地引擎离线、不要 Key；API 引擎走任意 OpenAI
// 兼容 /audio/transcriptions，准确率更高但音频要发给服务商。模型是否装好由
// /api/speech/status 直接读盘回答，下载进度由 /api/speech/model/download 轮询。
const ENGINE_OPTIONS: [string, string][] = [
  ['local', '本地模型（离线，默认）'],
  ['api', '模型 API（云端）'],
  ['auto', '自动（配好 API 就用 API）'],
]

/** 文本类字段：本地草稿 + 「保存」才写盘（每敲一个字就校验太吵）。 */
function DraftInput(props: {
  fieldKey: string
  draft: Record<string, string>
  current: string
  placeholder?: string
  type?: 'text' | 'password'
  onDraft: (key: string, value: string) => void
  onCommit: () => void
}) {
  const raw = props.draft[props.fieldKey]
  const value = raw !== undefined ? raw : props.current
  return (
    <input
      type={props.type ?? 'text'}
      value={value}
      placeholder={props.placeholder}
      autoComplete="off"
      onChange={(e) => props.onDraft(props.fieldKey, e.target.value)}
      onBlur={props.onCommit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') props.onCommit()
      }}
    />
  )
}

export function SpeechPage(props: { onBack: () => void }) {
  const { fields, values, error, saved, setField } = useConfigTopic('speech')
  const st = useAsync(() => api.speechStatus(), [])

  // 文本字段草稿：只有点「保存」/ 回车 / 失焦才提交，避免逐字 PUT。
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [rowError, setRowError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const download = st.data?.download

  // 下载在后台线程里跑：只要还在 downloading/extracting 就每 1.5 秒拉一次进度，
  // 结束了再刷新一次整体状态（模型出现在新目录后 ok 才算数）。
  useEffect(() => {
    const phase = download?.phase
    if (phase !== 'downloading' && phase !== 'extracting') return
    const timer = setTimeout(() => {
      void api
        .speechDownloadProgress()
        .then(() => st.reload())
        .catch(() => st.reload())
    }, 1500)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [download?.phase, download?.elapsedMs])

  // 下载结束：重新读盘，让「模型文件」那一行和识别器状态跟上。
  const prevPhase = useRef(download?.phase)
  useEffect(() => {
    const phase = download?.phase
    if (phase === 'done' && prevPhase.current !== 'done') st.reload()
    prevPhase.current = phase
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [download?.phase])

  if (!fields) return <DetailShell title="语音识别" onBack={props.onBack}><Spinner /></DetailShell>

  const info = st.data
  const str = (path: string, fallback = '') => String(values[path] ?? fallback)
  const engineSetting = str('speech.engine', 'local')
  const preset = str('speech.apiPreset', 'local')
  const presetDefaults = info?.presetDefaults ?? {}
  const presetDefault = presetDefaults[preset] ?? { base_url: '', model: '', key_env: '' }
  const hasApiKey = !!fields.find((f) => f.path === 'speech.apiKey')?.hasSecret
  const slotModel = info?.slotModel

  const commit = (path: string) => {
    const next = draft[path]
    if (next === undefined || next === str(path)) {
      setDraft((d) => {
        const { [path]: _drop, ...rest } = d
        return rest
      })
      return
    }
    void setField(path, next).then((ok) => {
      if (ok) {
        setDraft((d) => {
          const { [path]: _drop, ...rest } = d
          return rest
        })
        st.reload()
      }
    })
  }

  const onDraft = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }))

  const startDownload = async () => {
    setStarting(true)
    setRowError(null)
    try {
      await api.speechDownloadModel()
    } catch (e) {
      setRowError((e as Error).message)
    } finally {
      setStarting(false)
      st.reload()
    }
  }

  const changePreset = async (name: string) => {
    setRowError(null)
    // 先存预设，再把地址/模型填成该预设的默认值（空 = 用预设自带的）。
    if (!(await setField('speech.apiPreset', name))) return
    const p = presetDefaults[name]
    if (!p) return
    setDraft({})
    if (str('speech.apiBaseUrl') && str('speech.apiBaseUrl') !== p.base_url) {
      await setField('speech.apiBaseUrl', '')
    }
    if (str('speech.apiModel') && str('speech.apiModel') !== p.model) {
      await setField('speech.apiModel', '')
    }
    // 选「本地」预设等于不用 API 了。
    if (name === 'local' && engineSetting === 'api') await setField('speech.engine', 'local')
    st.reload()
  }

  const busy = download?.phase === 'downloading' || download?.phase === 'extracting'
  const isLocal = info?.engine !== 'api'

  return (
    <DetailShell
      title="语音识别"
      subtitle="输入框 🎤 按钮与 transcribe_audio 工具共用。默认走本地 sherpa-onnx 中英双语模型（离线，音频不出本机）；也可以切成模型 API。"
      onBack={props.onBack}
      extra={
        <div className="row-actions">
          <button className="btn-soft" onClick={st.reload} disabled={st.loading}>刷新</button>
        </div>
      }
    >
      <StatusLine ok={saved} err={error ?? rowError} />

      <SectionCard title="识别引擎" hint={info ? `当前：${info.engines[info.engine]?.label ?? info.engine}` : undefined}>
        {st.loading && !info ? (
          <Spinner />
        ) : st.error ? (
          <p className="error">{st.error}</p>
        ) : info && (
          <>
            <Row label="引擎" desc="本地 = 离线模型；API = 把音频发给你选的语音接口。改动立即生效。">
              <select value={engineSetting} onChange={(e) => void setField('speech.engine', e.target.value)}>
                {ENGINE_OPTIONS.map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            </Row>
            <Row
              label="就绪状态"
              desc={
                info.engine === 'api'
                  ? [info.api?.model, info.api?.baseUrl].filter(Boolean).join(' · ') || '接口未配置'
                  : info.root ?? info.managedDir
              }
            >
              <span className={`pill ${info.ok ? 'ok' : 'warn'}`}>
                {info.ok ? '可用' : '不可用'}
              </span>
            </Row>
            <Row label="识别器" desc="本地引擎首次调用时加载权重（约 3–5 秒），之后常驻复用。">
              <span className={`pill ${info.loaded ? 'ok' : ''}`}>
                {info.engine === 'api' ? `API（${info.api?.auth === 'studio' ? 'Studio 代理' : 'Key'}）` : info.loaded ? '已加载' : '未加载'}
              </span>
            </Row>
            <Row label="采样率 / 语言" desc="浏览器按这个采样率录音，所以不需要重采样。">
              <code>
                {info.sampleRate} Hz · {info.languages.join(' / ')}
              </code>
            </Row>
            {info.error && <p className="error">{info.error}</p>}
            {info.warnings.length > 0 && (
              <Note kind="warn">
                {info.warnings.map((w) => (
                  <span key={w}>
                    {w}
                    <br />
                  </span>
                ))}
              </Note>
            )}
          </>
        )}
      </SectionCard>

      {!isLocal && (
        <SectionCard title="模型 API">
          <Row label="服务商预设" desc={preset === 'studio' ? '自动读取本机 Ekko Studio 的语音代理，不用填 Key。' : preset === 'model' ? '直接用「设置 → 模型服务 → 用途分槽 → 音频」绑定的那个模型，Key / 地址都从厂商实例读，下面三项不用填。' : presetDefault.key_env ? `留空 Key 时自动读环境变量 ${presetDefault.key_env}。` : '选好预设后，地址和模型留空就用预设的。'}>
            <select value={preset} onChange={(e) => void changePreset(e.target.value)}>
              {Object.entries(info?.presets ?? {}).map(([name, label]) => (
                <option key={name} value={name}>{label}</option>
              ))}
            </select>
          </Row>
          {preset === 'model' && (
            <Row
              label="音频槽位"
              desc={slotModel?.bound ? `${slotModel.endpoint}` : '还没绑定：去「模型服务 → 用途分槽 → 音频」选厂商实例 + 模型。'}
            >
              <span className={`pill ${slotModel?.bound ? 'ok' : 'warn'}`}>
                {slotModel?.bound ? `${slotModel.instanceId} · ${slotModel.model}` : '未绑定'}
              </span>
            </Row>
          )}
          {preset !== 'model' && (
          <>
          <Row label="接口地址" desc="完整的 /audio/transcriptions 地址。" >
            <DraftInput
              fieldKey="speech.apiBaseUrl"
              draft={draft}
              current={str('speech.apiBaseUrl')}
              placeholder={presetDefault.base_url || 'https://…/v1/audio/transcriptions'}
              onDraft={onDraft}
              onCommit={() => commit('speech.apiBaseUrl')}
            />
          </Row>
          <Row label="模型名" desc="例如 gpt-4o-transcribe、whisper-large-v3-turbo。">
            <DraftInput
              fieldKey="speech.apiModel"
              draft={draft}
              current={str('speech.apiModel')}
              placeholder={presetDefault.model || '模型 ID'}
              onDraft={onDraft}
              onCommit={() => commit('speech.apiModel')}
            />
          </Row>
          <Row
            label="API Key"
            desc={hasApiKey ? '已保存（显示为占位符）。想换 Key 就直接输入新值；留空 = 不变。' : preset === 'studio' ? 'Studio 预设一般不用填。' : '保存后只显示占位符，明文不会回到浏览器。'}
          >
            <DraftInput
              fieldKey="speech.apiKey"
              draft={draft}
              current={hasApiKey ? '' : str('speech.apiKey')}
              type="password"
              placeholder={hasApiKey ? '•••••••• (已配置，留空不变)' : 'sk-…'}
              onDraft={onDraft}
              onCommit={() => commit('speech.apiKey')}
            />
          </Row>
          </>
          )}
          <Row label="识别语言" desc="zh / en…；留空让接口自己判断。">
            <DraftInput
              fieldKey="speech.apiLanguage"
              draft={draft}
              current={str('speech.apiLanguage')}
              placeholder="zh"
              onDraft={onDraft}
              onCommit={() => commit('speech.apiLanguage')}
            />
          </Row>
          <Row label="提示词" desc="给模型的上下文（专有名词等），可留空。">
            <DraftInput
              fieldKey="speech.apiPrompt"
              draft={draft}
              current={str('speech.apiPrompt')}
              placeholder="例如：Ekko、OpenMinis"
              onDraft={onDraft}
              onCommit={() => commit('speech.apiPrompt')}
            />
          </Row>
          <Row label="超时（秒）" desc="等接口返回的最长秒数。">
            <input
              type="number" min={5} max={600}
              value={Number(str('speech.apiTimeoutSeconds', '120'))}
              onChange={(e) => void setField('speech.apiTimeoutSeconds', Number(e.target.value))}
            />
          </Row>
          <Note kind="info">
            文本输入框改完点「保存」或直接回车；切换预设会把地址 / 模型重置成该预设的默认值。
          </Note>
          <div className="row-actions">
            <button
              className="btn-soft"
              disabled={Object.keys(draft).length === 0}
              onClick={() => {
                for (const k of Object.keys(draft)) commit(k)
              }}
            >
              保存所有修改
            </button>
          </div>
        </SectionCard>
      )}

      <SectionCard
        title="本地模型"
        hint={info ? `下载到 ${info.managedDir}` : undefined}
        extra={
          <button onClick={() => void startDownload()} disabled={busy || starting}>
            {busy ? '下载中…' : info?.ok && info.engine !== 'api' ? '重新下载' : '下载模型'}
          </button>
        }
      >
        {info && (
          <>
            <Row label="模型" desc={info.root ?? `尚未安装，点右上角「下载模型」（约 200 MB，装好后离线可用）`}>
              <span className={`pill ${info.localReady ? 'ok' : 'warn'}`}>
                {info.localReady ? '文件齐全' : '未安装'}
              </span>
            </Row>
            {download && download.phase !== 'idle' && (
              <div className="download-block">
                <MiniBar
                  label={download.phase === 'extracting' ? '解压中' : download.phase === 'done' ? '已完成' : '下载中'}
                  frac={download.percent ? download.percent / 100 : 0}
                  bytes={download.received}
                  sub={
                    download.phase === 'downloading'
                      ? `${download.percent ?? 0}% · ${fmtBytes(download.speedBytesPerSec)}/s · ${download.source ?? ''}`
                      : download.message || download.phase
                  }
                />
                {download.phase === 'error' && (
                  <p className="error">
                    {download.error ?? download.message}
                    {download.source && <>（来源：{download.source}）</>}
                  </p>
                )}
              </div>
            )}
            <Note kind="info">
              下载源依次尝试 GitHub Release、镜像与 Hugging Face 镜像；装到数据目录下的 models/，
              不改动程序目录。装好后点「刷新」即可看到「文件齐全」。
            </Note>
          </>
        )}
      </SectionCard>

      <SectionCard title="本地识别参数">
        {fields.map((f) => {
          const value = values[f.path]
          if (f.path === 'speech.enabled') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <input
                  type="checkbox"
                  checked={!!value}
                  onChange={(e) => void setField(f.path, e.target.checked)}
                />
              </Row>
            )
          }
          if (f.path === 'speech.numThreads') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <input
                  type="number" min={1} max={16}
                  value={Number(value ?? 2)}
                  onChange={(e) => void setField(f.path, Number(e.target.value))}
                />
              </Row>
            )
          }
          if (f.path === 'speech.decodingMethod') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <select
                  value={String(value ?? 'greedy_search')}
                  onChange={(e) => void setField(f.path, e.target.value)}
                >
                  <option value="greedy_search">greedy_search（最快）</option>
                  <option value="modified_beam_search">modified_beam_search（更准）</option>
                </select>
              </Row>
            )
          }
          if (f.path === 'speech.flushSilenceSeconds') {
            return (
              <Row key={f.path} label={f.displayName} desc={f.description}>
                <div className="range-wrap">
                  <input
                    type="range" min={0} max={3} step={0.1}
                    value={Number(value ?? 0.8)}
                    onChange={(e) => void setField(f.path, Number(e.target.value))}
                  />
                  <code>{Number(value ?? 0.8).toFixed(1)}s</code>
                </div>
              </Row>
            )
          }
          return null
        })}
      </SectionCard>

      <Note kind="info">
        线程数 / 解码方式改动后，下一次识别会重建识别器（约 5 秒）；切到 API 会顺手释放
        本地模型占用的内存。关掉「启用」则 🎤 按钮与 transcribe_audio 工具都会明确拒绝，
        而不是静默失败。
      </Note>
      {/* 「保存所有修改」以外还有零散草稿时提示一下，免得以为已经生效。 */}
      {Object.keys(draft).length > 0 && (
        <Note kind="warn">有 {Object.keys(draft).length} 项修改还没保存（回车或点「保存所有修改」）。</Note>
      )}
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
export function McpPage(props: { onBack: () => void }) {
  return (
    <DetailShell
      title="MCP 集成"
      subtitle="对应 Android 的 MCPIntegrationsScreen。"
      onBack={props.onBack}
    >
      <Note kind="warn">
        Python 侧 MCP 服务器接入尚未移植(openminis/mcp 为占位)。移植后会在此列出已连接的
        MCP 服务器、状态与工具数,并提供添加/授权入口。
      </Note>
      <SectionCard title="规划">
        <ul className="todo-list muted">
          <li>MCP 配置存放与服务器生命周期管理(mcp.json)</li>
          <li>OAuth 设备流登录(与各 LLM 厂商授权共用)</li>
          <li>工具 schema 合并进 Agent 技能目录</li>
        </ul>
      </SectionCard>
    </DetailShell>
  )
}

// ---------------------------------------------------------------------------
// 关于
// ---------------------------------------------------------------------------
export function AboutPage(props: { onBack: () => void }) {
  const { data, error, loading } = useAsync(() => api.appinfo())
  return (
    <DetailShell title="关于 OpenMinis" subtitle="Python 移植版信息。" onBack={props.onBack}>
      {loading ? <Spinner /> : error ? <p className="error">{error}</p> : data && (
        <>
          <SectionCard title={`${data.name} ${data.version}`}>
            <KvFacts
              rows={[
                ['构建', `v${data.version} (${data.versionCode}) · ${data.packageName}`],
                ['Python', data.python],
                ['平台', data.platform],
                ['数据目录', data.dataDir],
                ['默认工作目录', data.workspaceDir],
                ['记忆目录', data.memoryDir],
                ['日志', data.logPath],
                ['设置文件', data.settingsFile],
              ]}
            />
          </SectionCard>
          <SectionCard title="项目">
            <p className="muted">
              本仓库（Python 移植）:{' '}
              <a href="https://github.com/littlhub/PythonOpenMinis" target="_blank" rel="noreferrer">
                github.com/littlhub/PythonOpenMinis
              </a>
              <br />
              上游原版:{' '}
              <a href="https://github.com/OpenMinis/OpenMinis" target="_blank" rel="noreferrer">
                github.com/OpenMinis/OpenMinis
              </a>
              <br />
              反馈:{' '}
              <a href="https://github.com/littlhub/PythonOpenMinis/issues/new" target="_blank" rel="noreferrer">
                littlhub/PythonOpenMinis Issues
              </a>
              <br />
              隐私政策:{' '}
              <a href="https://openminis.github.io/privacy-policy.html" target="_blank" rel="noreferrer">
                openminis.github.io/privacy-policy.html
              </a>
            </p>
          </SectionCard>
        </>
      )}
    </DetailShell>
  )
}
