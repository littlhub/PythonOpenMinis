import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type {
  ProviderInfo,
  RepeatMode,
  ScheduledRunInfo,
  ScheduledTaskDraft,
  ScheduledTaskInfo,
} from '../types'

/**
 * SchedulerView - 定时任务
 *
 * 数据来自后端「<data_dir>/scheduled/tasks.json」（经 /api/scheduled/tasks）。
 * 之前这一页是写死在组件 state 里的假数据，删除只改内存，刷新就复活 —— 现在
 * 所有增删改都落盘，服务端还有后台 ticker 负责到点执行 prompt。
 */
const REPEAT_LABELS: Record<RepeatMode, string> = {
  ONCE: '一次',
  DAILY: '每天',
  WEEKDAYS: '工作日',
  CUSTOM: '自定义',
}

// Calendar.DAY_OF_WEEK: 1=周日 … 7=周六
const DAY_OPTIONS = [
  { value: 2, label: '一' },
  { value: 3, label: '二' },
  { value: 4, label: '三' },
  { value: 5, label: '四' },
  { value: 6, label: '五' },
  { value: 7, label: '六' },
  { value: 1, label: '日' },
]

const pad = (n: number) => String(n).padStart(2, '0')

function formatNext(ms: number | null, enabled: boolean): string {
  if (!enabled) return '已暂停'
  if (!ms) return '无有效档期'
  const d = new Date(ms)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const tomorrow = new Date(now.getTime() + 86400000).toDateString() === d.toDateString()
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (sameDay) return `今天 ${hm}`
  if (tomorrow) return `明天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
}

function formatFired(ms: number): string {
  const d = new Date(ms)
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const EMPTY_DRAFT: ScheduledTaskDraft = {
  label: '',
  hour: 9,
  minute: 0,
  repeatMode: 'DAILY',
  customDays: [],
  prompt: '',
  modelId: null,
  modelBinding: null,
}

/**
 * 一个可选模型。``value`` 编码成 ``实例id|模型id`` 而不是裸模型 id ——
 * 同一个模型名可能同时挂在两个 OpenAI 兼容网关上,只有实例能决定用哪个
 * key 和 base URL。空字符串代表「跟随当前模型」。
 */
interface TaskModelChoice {
  value: string
  label: string
}

const FOLLOW_CURRENT = ''

function encodeChoice(instanceId: string, modelId: string): string {
  return `${instanceId}|${modelId}`
}

function decodeChoice(value: string): { modelId: string | null; modelBinding: string | null } {
  if (!value) return { modelId: null, modelBinding: null }
  const idx = value.indexOf('|')
  if (idx < 0) return { modelId: value || null, modelBinding: null }
  const instanceId = value.slice(0, idx)
  return { modelBinding: instanceId || null, modelId: value.slice(idx + 1) || null }
}

function choiceLabel(task: ScheduledTaskInfo): string | null {
  if (!task.modelId) return null
  return task.modelId
}

/** 把已配置的 provider 实例摊平成下拉选项。
 *
 * 只列引擎已移植且填了 key 的实例:定时任务在后台跑,列一个必然失败的选项
 * 只会让用户半夜收到一条「模型服务未配置」而不知道是自己选错了。 */
function buildModelChoices(providers: ProviderInfo[]): TaskModelChoice[] {
  const out: TaskModelChoice[] = []
  for (const p of providers) {
    if (!p.engine || !p.hasKey) continue
    const seen = new Set<string>()
    const push = (id: string, name?: string) => {
      if (!id || seen.has(id)) return
      seen.add(id)
      out.push({ value: encodeChoice(p.id, id), label: `${p.label} · ${name || id}` })
    }
    if (p.model) push(p.model)
    for (const m of p.models) push(m.id, m.name)
  }
  return out
}

export function SchedulerView() {
  const [tasks, setTasks] = useState<ScheduledTaskInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [showCreate, setShowCreate] = useState(false)
  const [draft, setDraft] = useState<ScheduledTaskDraft>(EMPTY_DRAFT)
  const [runsFor, setRunsFor] = useState<string | null>(null)
  const [runs, setRuns] = useState<ScheduledRunInfo[]>([])
  const [modelChoices, setModelChoices] = useState<TaskModelChoice[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [r, s] = await Promise.all([api.scheduledList(), api.settingsGet()])
      setTasks(r.tasks)
      setModelChoices(buildModelChoices(s.providers))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const createTask = async () => {
    if (!draft.label.trim() || !draft.prompt.trim()) {
      setError('请填写任务名称与提示词。')
      return
    }
    if (draft.repeatMode === 'CUSTOM' && !(draft.customDays ?? []).length) {
      setError('自定义重复请至少选择一天。')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.scheduledCreate(draft)
      setDraft(EMPTY_DRAFT)
      setShowCreate(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const deleteTask = async (t: ScheduledTaskInfo) => {
    if (!window.confirm(`删除定时任务「${t.label}」？`)) return
    setBusy(true)
    setError('')
    try {
      await api.scheduledDelete(t.id)
      if (runsFor === t.id) {
        setRunsFor(null)
        setRuns([])
      }
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const toggleTask = async (t: ScheduledTaskInfo) => {
    setBusy(true)
    setError('')
    try {
      await api.scheduledToggle(t.id, !t.enabled)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const openRuns = async (t: ScheduledTaskInfo) => {
    if (runsFor === t.id) {
      setRunsFor(null)
      return
    }
    try {
      const r = await api.scheduledRuns(t.id)
      setRuns(r.runs)
      setRunsFor(t.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const toggleDay = (d: number) => {
    const cur = draft.customDays ?? []
    setDraft({
      ...draft,
      customDays: cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d],
    })
  }

  const active = tasks.filter((t) => t.enabled).length

  const draftValue = draft.modelId
    ? encodeChoice(draft.modelBinding ?? '', draft.modelId)
    : FOLLOW_CURRENT
  // 钉住的模型可能已从配置里消失(实例被删/改了 model)。那时 select 里没有
  // 对应 option,浏览器会显示空白 —— 补一条说明,而不是让人以为没选。
  const draftKnown = !draft.modelId || modelChoices.some((c) => c.value === draftValue)

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>⏰ 定时任务</h2>
          <span className="kb-count">
            {loading ? '加载中…' : `${active} 个运行中 · 共 ${tasks.length} 个`}
          </span>
        </div>

        {error && <div className="note note-warn">{error}</div>}

        {/* 创建新任务 */}
        {!showCreate ? (
          <button className="memory-add-btn" onClick={() => setShowCreate(true)}>
            ＋ 新建任务
          </button>
        ) : (
          <div className="create-task-form">
            <h3>新建定时任务</h3>
            <div className="form-group">
              <label>任务名称</label>
              <input
                type="text"
                placeholder="例如：每日早报"
                value={draft.label}
                onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label>提示词（到点时交给 Agent 执行）</label>
              <textarea
                rows={3}
                placeholder="例如：总结今天的新闻要点"
                value={draft.prompt}
                onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label>重复方式</label>
              <select
                value={draft.repeatMode}
                onChange={(e) =>
                  setDraft({ ...draft, repeatMode: e.target.value as RepeatMode })
                }
              >
                {(Object.keys(REPEAT_LABELS) as RepeatMode[]).map((m) => (
                  <option key={m} value={m}>
                    {REPEAT_LABELS[m]}
                  </option>
                ))}
              </select>
            </div>
            {draft.repeatMode === 'CUSTOM' && (
              <div className="form-group">
                <label>选择星期</label>
                <div className="tool-chips">
                  {DAY_OPTIONS.map((d) => (
                    <button
                      type="button"
                      key={d.value}
                      className={`chip ${(draft.customDays ?? []).includes(d.value) ? 'on' : ''}`}
                      onClick={() => toggleDay(d.value)}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="form-group">
              <label>模型</label>
              <select
                value={draft.modelId ? encodeChoice(draft.modelBinding ?? '', draft.modelId) : FOLLOW_CURRENT}
                onChange={(e) => setDraft({ ...draft, ...decodeChoice(e.target.value) })}
              >
                <option value={FOLLOW_CURRENT}>跟随当前模型</option>
                {!draftKnown && (
                  <option value={draftValue}>{draft.modelId}（当前配置里找不到）</option>
                )}
                {modelChoices.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
              <div className="muted form-hint">
                任务在后台无人值守执行。钉住模型后,即使你之后切换「当前模型」,
                它仍用创建时选的那个。
              </div>
            </div>
            <div className="form-group">
              <label>触发时间</label>
              <div className="row-actions">
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={draft.hour}
                  onChange={(e) => setDraft({ ...draft, hour: Number(e.target.value) || 0 })}
                />
                <span>:</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  value={draft.minute}
                  onChange={(e) => setDraft({ ...draft, minute: Number(e.target.value) || 0 })}
                />
              </div>
            </div>
            <div className="form-actions">
              <button className="btn-cancel" onClick={() => setShowCreate(false)}>
                取消
              </button>
              <button className="btn-create" disabled={busy} onClick={() => void createTask()}>
                创建
              </button>
            </div>
          </div>
        )}

        {/* 任务列表 */}
        <div className="tasks-list">
          {loading ? (
            <div className="kb-empty">加载中…</div>
          ) : tasks.length === 0 ? (
            <div className="kb-empty">暂无定时任务</div>
          ) : (
            tasks.map((task) => (
              <div key={task.id} className={`task-card ${task.enabled ? 'active' : 'paused'}`}>
                <div className="task-info">
                  <div className="task-name">
                    {task.label}
                    {!task.enabled && <span className="pill">已暂停</span>}
                  </div>
                  <div className="task-meta">
                    <span className="task-type">{REPEAT_LABELS[task.repeatMode] ?? task.repeatMode}</span>
                    <span className="task-schedule">
                      {pad(task.hour)}:{pad(task.minute)}
                      {task.customDays ? ` · 周${task.customDays.split(',').length}天` : ''}
                    </span>
                    <span className="task-next">下次运行: {formatNext(task.nextTriggerMs, task.enabled)}</span>
                    <span className="task-model">
                      模型: {choiceLabel(task) ?? '跟随当前'}
                    </span>
                  </div>
                  <div className="task-prompt muted">{task.prompt}</div>
                  {task.lastResultPreview && (
                    <div className="task-prompt muted">上次结果: {task.lastResultPreview}</div>
                  )}
                </div>
                <div className="task-actions">
                  <button
                    className={`task-toggle ${task.enabled ? 'active' : ''}`}
                    disabled={busy}
                    onClick={() => void toggleTask(task)}
                  >
                    {task.enabled ? '暂停' : '启用'}
                  </button>
                  <button className="link" onClick={() => void openRuns(task)}>
                    记录({task.runCount})
                  </button>
                  <button className="task-delete" disabled={busy} onClick={() => void deleteTask(task)}>
                    删除
                  </button>
                </div>
                {runsFor === task.id && (
                  <div className="task-runs">
                    {runs.length === 0 ? (
                      <div className="muted">还没有执行记录</div>
                    ) : (
                      runs.map((r, i) => (
                        <div key={`${r.firedAt}-${i}`} className="task-run-row">
                          <span className={r.ok ? 'run-ok' : 'run-bad'}>{r.ok ? '✓' : '✕'}</span>
                          <span className="muted">{formatFired(r.firedAt)}</span>
                          <span>{r.preview || '（无预览）'}</span>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
