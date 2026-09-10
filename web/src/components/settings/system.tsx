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
        await api.configSet(path, value)
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
          <div key={i} className="env-row">
            <input placeholder="KEY" className="mono" value={k} onChange={(e) => patch(i, 0, e.target.value)} />
            <input placeholder="value" className="mono" value={v} onChange={(e) => patch(i, 1, e.target.value)} />
            <button className="link danger" onClick={() => setRows((prev) => prev.filter((_, idx) => idx !== i))}>移除</button>
          </div>
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
  const { data: server, error, loading } = useAsync<ConfigFieldInfo[]>(() => api.configList('server'))
  return (
    <DetailShell
      title="后台运行与服务"
      subtitle="对应 Android 的 Background & Notifications(电池优化/自启动引导)——桌面进程常驻,不适用;此处为服务信息。"
      onBack={props.onBack}
    >
      {loading ? <Spinner /> : error ? <p className="error">{error}</p> : (
        <SectionCard title="本地服务">
          {server?.map((f) => (
            <Row key={f.path} label={f.displayName} desc={f.description}>
              <code>{String(f.value)}</code>
            </Row>
          ))}
        </SectionCard>
      )}
      <Note kind="info">
        网页由本地 FastAPI 服务(run.bat)提供,端口改动后需重启服务生效。关闭浏览器不会中断对话;
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
              开源仓库:{' '}
              <a href="https://github.com/OpenMinis/OpenMinis" target="_blank" rel="noreferrer">
                github.com/OpenMinis/OpenMinis
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
