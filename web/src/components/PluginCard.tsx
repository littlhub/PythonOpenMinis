import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { PluginFieldInfo, PluginLogEntry, PluginStatus } from '../types'

/**
 * PluginCard —— 一个插件的「配置 + 起停 + 日志」卡片。
 *
 * 「通道」页与「插件」页共用它：通道页只筛 `category === 'channel'` 的那批
 * （QQ 机器人就在那儿装上、填 AppID/密钥、点启动），插件页展示全部 —— 包括
 * 导入进来的第三方包、外部程序插件，以及**给 agent 加工具**的插件。
 *
 * 状态一律来自后端 `/api/plugins`，界面不自己编造 —— 装没装、在不在跑、
 * 差哪些必填项都是真实读出来的。
 */

const CATEGORY_LABEL: Record<string, string> = {
  channel: '通道',
  tool: '工具',
  other: '其他',
}

const RUNTIME_LABEL: Record<string, string> = {
  engine: '引擎内驱动',
  process: '外部程序',
  manual: '无连接',
}

/** 配置项在表单里的初值（list/csv 用换行/逗号文本编辑，保存时后端会收口）。 */
function initialValue(field: PluginFieldInfo, raw: unknown): string | boolean {
  if (field.type === 'switch') return Boolean(raw)
  if (field.type === 'list') {
    return Array.isArray(raw) ? raw.join('\n') : String(raw ?? '')
  }
  if (field.type === 'csv') {
    return Array.isArray(raw) ? raw.join(', ') : String(raw ?? '')
  }
  if (raw === null || raw === undefined) return ''
  return String(raw)
}

/** 表单里的值 → 提交给后端的形状（list 拆行、csv 拆逗号、number 转数字）。 */
function submitValue(field: PluginFieldInfo, value: string | boolean): unknown {
  if (field.type === 'switch') return Boolean(value)
  if (field.type === 'number') {
    const n = parseInt(String(value), 10)
    return Number.isFinite(n) ? n : undefined
  }
  if (field.type === 'list') {
    return String(value)
      .split('\n')
      .map((v) => v.trim())
      .filter(Boolean)
  }
  if (field.type === 'csv') {
    return String(value)
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean)
  }
  return String(value)
}

export function PluginCard({
  plugin,
  onChanged,
  onNotice,
  defaultOpen = false,
}: {
  plugin: PluginStatus
  /** 保存配置 / 起停之后回调，调用方重新拉列表。 */
  onChanged: () => void | Promise<void>
  onNotice?: (message: string, bad?: boolean) => void
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const [busy, setBusy] = useState('')
  const [logs, setLogs] = useState<PluginLogEntry[] | null>(null)
  const [form, setForm] = useState<Record<string, string | boolean>>({})

  const fields = plugin.fields ?? []
  const tools = plugin.tools ?? []
  /** 有可以起的东西才给启停按钮：通道/外部程序，或有工具（工具插件按需起进程）。 */
  const canStart = plugin.runtime !== 'manual' || tools.length > 0

  const resetForm = useMemo(
    () => () => {
      const next: Record<string, string | boolean> = {}
      for (const f of fields) next[f.key] = initialValue(f, plugin.config?.[f.key])
      setForm(next)
    },
    // plugin.config 每次 load 都是新对象，用它当依赖即可
    [fields, plugin.config],
  )

  useEffect(() => {
    resetForm()
  }, [resetForm])

  const state = useMemo(() => {
    if (!plugin.installed) return { cls: 'disconnected', text: '未安装' }
    if (plugin.running) {
      return { cls: 'connected', text: plugin.detail || '运行中' }
    }
    if (plugin.state === 'error') {
      return { cls: 'disconnected', text: plugin.error || '出错了' }
    }
    if (plugin.error) return { cls: 'disconnected', text: plugin.error }
    if (plugin.enabled) {
      // 纯工具插件没有「在跑」的概念 —— 启用 = 工具已挂给 agent。
      if (plugin.toolCount > 0 && plugin.runtime === 'manual') {
        return { cls: 'connected', text: `已启用 · ${plugin.toolCount} 个工具` }
      }
      return { cls: '', text: '已启用，未运行' }
    }
    if (plugin.toolCount > 0) return { cls: '', text: '已停止（工具未挂载）' }
    return { cls: '', text: '已停止' }
  }, [plugin])

  const set = (key: string, value: string | boolean) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    try {
      await fn()
      await onChanged()
    } catch (e) {
      onNotice?.(e instanceof Error ? e.message : String(e), true)
    } finally {
      setBusy('')
    }
  }

  const doInstall = () =>
    run('install', async () => {
      const r = await api.pluginInstall(plugin.id)
      onNotice?.(`已安装插件「${r.plugin.name || plugin.id}」，填好配置后点启动`)
    })

  /** 内置插件的清单更新 —— 已填的配置会保留，只把清单换成包内那份。 */
  const doRefresh = () =>
    run('refresh', async () => {
      const r = await api.pluginInstall(plugin.id, true)
      onNotice?.(`已把「${r.plugin.name || plugin.id}」的清单更新到内置版本（配置已保留）`)
    })

  const doRemove = () => {
    if (!confirm(`卸载插件「${plugin.name}」？插件目录与它的配置都会被删除。`)) return
    return run('remove', async () => {
      await api.pluginRemove(plugin.id)
      onNotice?.(`已卸载 ${plugin.name}`)
    })
  }

  const doAction = (action: 'start' | 'stop' | 'restart') =>
    run(action, async () => {
      const r = await api.pluginAction(plugin.id, action)
      const label = { start: '已启动', stop: '已停止', restart: '已重启' }[action]
      onNotice?.(`${label} ${r.plugin.name || plugin.id}${r.plugin.error ? `：${r.plugin.error}` : ''}`)
    })

  const doSave = () => {
    const values: Record<string, unknown> = {}
    for (const f of fields) {
      const v = submitValue(f, form[f.key])
      // 密钥留空 = 不改（界面拿到的是空串，别把已存的密钥冲掉）
      if (f.secret && typeof v === 'string' && !v.trim()) continue
      values[f.key] = v
    }
    return run('save', async () => {
      const r = await api.pluginConfigSave(plugin.id, values)
      // 以服务端返回的配置回填：被丢掉的项要立刻从界面消失，而不是假装存上了
      const next: Record<string, string | boolean> = {}
      for (const f of r.plugin.fields ?? fields) {
        next[f.key] = initialValue(f, r.plugin.config?.[f.key])
      }
      setForm(next)
      onNotice?.(
        r.plugin.running ? '已保存，插件已按新配置重连' : '已保存',
      )
    })
  }

  const loadLogs = () =>
    run('logs', async () => {
      const r = await api.pluginLogs(plugin.id, 120)
      setLogs(r.logs)
    })

  const toggleOpen = () => {
    const next = !open
    setOpen(next)
    if (next && logs === null) void loadLogs()
  }

  return (
    <div className={`plugin-card ${plugin.running ? 'is-running' : ''}`}>
      <div className="plugin-head">
        <div className="plugin-icon">{plugin.icon || '🔌'}</div>
        <div className="plugin-info">
          <div className="plugin-name">
            {plugin.name}
            <span className="plugin-chip">{CATEGORY_LABEL[plugin.category] ?? plugin.category}</span>
            <span className="plugin-chip subtle">
              {RUNTIME_LABEL[plugin.runtime] ?? plugin.runtime}
            </span>
            {plugin.installed && <span className="plugin-chip subtle">v{plugin.version}</span>}
          </div>
          <div className="plugin-desc" title={plugin.description}>
            {plugin.description || '（无描述）'}
          </div>
          {plugin.process?.command && (
            <div className="plugin-desc mono" title={(plugin.process.args ?? []).join(' ')}>
              $ {plugin.process.command} {(plugin.process.args ?? []).join(' ')}
              {plugin.runtime === 'process' && plugin.pid ? ` · pid ${plugin.pid}` : ''}
            </div>
          )}
        </div>
        <div className="channel-status">
          <span className={`status-dot ${state.cls}`} />
          <span className="status-text">{state.text}</span>
        </div>
      </div>

      {plugin.installed && plugin.outdated && (
        <div className="note note-info" style={{ marginTop: 8 }}>
          这个内置插件的清单有新版（可能多了配置项）。点「更新清单」换上，已填的配置会保留。
        </div>
      )}

      {plugin.installed && plugin.missing.length > 0 && (
        <div className="note note-warn" style={{ marginTop: 8 }}>
          还差必填项：{plugin.missing.join('、')}
        </div>
      )}

      <div className="plugin-actions">
        {!plugin.installed ? (
          <button className="plugin-btn primary" disabled={busy !== ''} onClick={() => void doInstall()}>
            {busy === 'install' ? '安装中…' : '安装'}
          </button>
        ) : (
          <>
            {plugin.outdated && plugin.builtin && (
              <button
                className="plugin-btn primary"
                disabled={busy !== ''}
                title="用内置那版清单覆盖已装的，配置会保留"
                onClick={() => void doRefresh()}
              >
                {busy === 'refresh' ? '更新中…' : '更新清单'}
              </button>
            )}
            {canStart &&
              (plugin.running ? (
                <button className="plugin-btn" disabled={busy !== ''} onClick={() => void doAction('stop')}>
                  {busy === 'stop' ? '停止中…' : '停止'}
                </button>
              ) : (
                <button
                  className={`plugin-btn ${plugin.enabled ? '' : 'primary'}`}
                  disabled={busy !== '' || plugin.missing.length > 0}
                  title={plugin.missing.length ? '先把必填项填上' : '启用插件'}
                  onClick={() => void doAction('start')}
                >
                  {busy === 'start' ? '启用中…' : plugin.enabled ? '重新启用' : '启用'}
                </button>
              ))}
            {plugin.running && (
              <button className="plugin-btn" disabled={busy !== ''} onClick={() => void doAction('restart')}>
                {busy === 'restart' ? '重启中…' : '重启'}
              </button>
            )}
            <button className="plugin-btn" onClick={toggleOpen}>
              {open ? '收起配置' : '配置'}
            </button>
            <button className="plugin-btn danger" disabled={busy !== ''} onClick={() => void doRemove()}>
              {busy === 'remove' ? '卸载中…' : '卸载'}
            </button>
          </>
        )}
      </div>

      {open && plugin.installed && (
        <div className="plugin-body">
          {tools.length > 0 && (
            <div className="plugin-tools">
              <div className="plugin-logs-head">
                <span>
                  给 agent 加的工具（{tools.length}）
                  {plugin.enabled ? ' · 已挂载' : ' · 未挂载，去「助理」的身份里勾选'}
                </span>
              </div>
              {tools.map((t) => {
                const params = Object.keys(t.parameters ?? {})
                return (
                  <div key={t.id} className="plugin-tool">
                    <span className="plugin-tool-name mono">
                      {plugin.id.replace(/[^a-zA-Z0-9_-]/g, '_')}__{t.id}
                    </span>
                    <span className="plugin-tool-desc" title={t.description}>
                      {t.description || '（无描述）'}
                    </span>
                    {params.length > 0 && (
                      <span className="muted" style={{ fontSize: 11 }}>
                        参数：{params.join('、')}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {fields.length === 0 ? (
            <div className="muted" style={{ fontSize: 12 }}>
              这个插件没有可配置项。{' '}
              {plugin.runtime === 'manual' && tools.length === 0
                ? '它没有识别出启动入口 —— 在插件目录里补一份带 process.command（或 tools）的 plugin.json 就能启用。'
                : ''}
            </div>
          ) : (
            <div className="plugin-form">
              {fields.map((f) => (
                <label key={f.key} className="plugin-field">
                  <span className="plugin-field-label">
                    {f.label}
                    {f.required && <i className="req">*</i>}
                  </span>
                  {f.type === 'switch' ? (
                    <span className="skill-toggle">
                      <input
                        type="checkbox"
                        checked={Boolean(form[f.key])}
                        onChange={(e) => set(f.key, e.target.checked)}
                      />
                      <span className="toggle-slider" />
                    </span>
                  ) : f.type === 'select' ? (
                    <select
                      value={String(form[f.key] ?? '')}
                      onChange={(e) => set(f.key, e.target.value)}
                    >
                      {/* 后端已按 optionsFrom 解析好一组真实可选值（身份 / 子代理） */}
                      {(() => {
                        const opts = f.options ?? []
                        const groups: { name: string; items: typeof opts }[] = []
                        for (const o of opts) {
                          const name = o.group || ''
                          const last = groups[groups.length - 1]
                          if (last && last.name === name) last.items.push(o)
                          else groups.push({ name, items: [o] })
                        }
                        return groups.map((g) =>
                          g.name ? (
                            <optgroup key={g.name} label={g.name}>
                              {g.items.map((o) => (
                                <option key={o.value} value={o.value} title={o.description}>
                                  {o.label}
                                </option>
                              ))}
                            </optgroup>
                          ) : (
                            g.items.map((o) => (
                              <option key={o.value} value={o.value} title={o.description}>
                                {o.label}
                              </option>
                            ))
                          ),
                        )
                      })()}
                    </select>
                  ) : f.type === 'list' ? (
                    <textarea
                      rows={3}
                      value={String(form[f.key] ?? '')}
                      placeholder={f.placeholder || '一行一个'}
                      onChange={(e) => set(f.key, e.target.value)}
                    />
                  ) : (
                    <input
                      type={f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'}
                      value={String(form[f.key] ?? '')}
                      min={f.min}
                      max={f.max}
                      placeholder={
                        f.secret && plugin.config?.[`${f.key}__set`]
                          ? '已保存，留空不改'
                          : f.placeholder || ''
                      }
                      onChange={(e) => set(f.key, e.target.value)}
                    />
                  )}
                  {f.help && <span className="plugin-field-help">{f.help}</span>}
                </label>
              ))}
              <div className="plugin-form-foot">
                <button className="plugin-btn primary" disabled={busy !== ''} onClick={() => void doSave()}>
                  {busy === 'save' ? '保存中…' : '保存配置'}
                </button>
                {plugin.running && (
                  <span className="muted" style={{ fontSize: 11 }}>
                    保存后会自动重启以套用新配置
                  </span>
                )}
              </div>
            </div>
          )}

          <div className="plugin-logs">
            <div className="plugin-logs-head">
              <span>运行日志</span>
              <button className="plugin-btn tiny" disabled={busy !== ''} onClick={() => void loadLogs()}>
                {busy === 'logs' ? '刷新中…' : '刷新'}
              </button>
            </div>
            {logs === null ? (
              <div className="muted" style={{ fontSize: 11 }}>还没读日志</div>
            ) : logs.length === 0 ? (
              <div className="muted" style={{ fontSize: 11 }}>
                暂无日志 —— 插件还没跑起来（启动后这里会显示连接过程）
              </div>
            ) : (
              <div className="plugin-log-list">
                {logs.slice().reverse().map((l, i) => (
                  <div key={`${l.ts}-${i}`} className={`plugin-log ${l.level}`}>
                    <span className="plugin-log-time">
                      {new Date(l.ts).toLocaleTimeString()}
                    </span>
                    <span className="plugin-log-text">{l.text}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
