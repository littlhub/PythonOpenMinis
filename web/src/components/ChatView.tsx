import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  api,
  openSocket,
  type OpenSocketHandle,
  type SocketState,
} from '../api'
import type {
  ChatMessageInfo,
  ChatSessionInfo,
  SessionScopedFrame,
  Speaker,
  SubagentInfo,
  WorkspaceInfo,
} from '../types'
import { RUNNING_SESSIONS_EVENT as RUNNING_EVENT } from '../types'
import { RightPanel } from './RightPanel'
import { openImagePreview } from './ImageLightbox'

// 多会话并行后 messages/seen 都按会话从 map 里取；没有内容时返回同一个空数组，
// 免得每次渲染新建引用、把子组件全带上重渲。
const EMPTY_MESSAGES: UiMessage[] = []
const EMPTY_SPEAKERS: Speaker[] = []

/** 还没有活动会话时的兜底桶（新建会话、连接断开这类跟会话无关的提示）。 */
const GLOBAL_SID = '__global__'

interface ChatViewProps {
  activeSessionId: string | null
  onChangeSession: (id: string) => void
}

interface UiMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  toolCalls?: ToolCallCard[]
  /** 本轮由工具**自动产出的图片**（后端随 toolEnd 帧推来的绝对路径）。
   *  模型不写 `![](路径)` 时，界面也要能自动把它们预览出来。 */
  generated?: string[]
  /** 群聊发言人。子代理消息才有；主代理/用户消息不填（＝主代理）。 */
  speaker?: Speaker
  /** 子代理房间 = 外层那次 `subagent_delegate` 调用的 id。 */
  roomId?: string
  /** 兜底模型切换提示（不是模型说的话，渲染成一条浅色小条）。 */
  fallbackNote?: string
  /** 子代理交给它的任务（subagentStart 帧带上来，显示在气泡头部）。 */
  subTask?: string
  /** 子代理在自己循环里调的工具（与主代理的 toolCalls 分开渲染）。 */
  subTools?: ToolCallCard[]
  /** 子代理是否已收尾（收尾后折叠它的工具过程）。 */
  subDone?: boolean
}

/** 主代理在群聊里的固定身份（用户是「你」，子代理各自有名字）。 */
const MAIN_SPEAKER: Speaker = {
  id: 'main',
  name: '主代理',
  emoji: '🧭',
  kind: 'main',
}
const USER_SPEAKER: Speaker = {
  id: 'user',
  name: '你',
  emoji: '🙋',
  kind: 'user',
}
/** 「显示子代理过程」开关 —— 群聊里子代理的发言/工具是否展开。 */
const SHOW_SUB_KEY = 'openminis:group:showSub'

/** 右侧「工具栏」（项目文件 / 历史提问）的展开状态 —— 桌面记用户的选择。 */
const RIGHT_OPEN_KEY = 'openminis:right-open'

/** 手机竖屏：窄屏 + 竖屏方向。凑齐时右侧工具栏默认收起 —— 它固定 300px
 *  宽，在竖屏手机上会把聊天区压成一条缝；要看再点展开即可。 */
const PHONE_PORTRAIT = '(max-width: 768px) and (orientation: portrait)'

function phonePortrait(): boolean {
  return window.matchMedia(PHONE_PORTRAIT).matches
}

/** 群成员的三类身份（**只是身份标签**，不新增可执行成员）：
 *  * ``agent`` —— 助理页配的子代理，会被委派、可分配项目；
 *  * ``bot``   —— 同样是子代理，只是标成「机器人」；
 *  * ``human`` —— 真人席位（只作为发言人进群，不干活）。 */
type GroupKind = 'agent' | 'bot' | 'human'

interface GroupMember {
  id: string
  kind: GroupKind
  /** 真人席位的名字（子代理用配置里的名字，不存这里）。 */
  name?: string
}

const GROUP_KIND_LABEL: Record<GroupKind, string> = {
  agent: 'Agent',
  bot: 'Bot',
  human: '人',
}
const GROUP_KIND_ICON: Record<GroupKind, string> = {
  agent: '🧭',
  bot: '⚙️',
  human: '🙋',
}

/** 群成员名单按会话存本地（刷新/切回同一个会话仍在群里）。 */
function groupKey(sessionId: string): string {
  return `openminis:group:members:${sessionId}`
}
function loadGroup(sessionId: string | null): GroupMember[] {
  if (!sessionId) return []
  try {
    const raw = localStorage.getItem(groupKey(sessionId))
    const parsed = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return []
    const out: GroupMember[] = []
    for (const item of parsed) {
      if (typeof item === 'string' && item) {
        // 老版本只存子代理 id 数组 —— 那时进群的都是「Agent」。
        out.push({ id: item, kind: 'agent' })
      } else if (item && typeof item === 'object') {
        const rec = item as Record<string, unknown>
        const id = typeof rec.id === 'string' ? rec.id : ''
        const kind = rec.kind
        if (!id) continue
        out.push({
          id,
          kind:
            kind === 'bot' || kind === 'human' || kind === 'agent'
              ? kind
              : 'agent',
          name: typeof rec.name === 'string' ? rec.name : undefined,
        })
      }
    }
    return out
  } catch {
    return []
  }
}
function saveGroup(sessionId: string | null, members: GroupMember[]): void {
  if (!sessionId) return
  try {
    localStorage.setItem(groupKey(sessionId), JSON.stringify(members))
  } catch {
    /* 隐私模式下写不了，忽略 */
  }
}

/** 「分配项目」：成员 id → 工作空间 id。 */
function memberProjectsKey(sessionId: string): string {
  return `openminis:group:projects:${sessionId}`
}
function loadMemberProjects(sessionId: string | null): Record<string, string> {
  if (!sessionId) return {}
  try {
    const raw = localStorage.getItem(memberProjectsKey(sessionId))
    const parsed = raw ? JSON.parse(raw) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === 'string' && v) out[k] = v
    }
    return out
  } catch {
    return {}
  }
}
function saveMemberProjects(
  sessionId: string | null,
  mapping: Record<string, string>,
): void {
  if (!sessionId) return
  try {
    localStorage.setItem(memberProjectsKey(sessionId), JSON.stringify(mapping))
  } catch {
    /* 忽略 */
  }
}

/** 找最后一条**主代理**消息的下标（子代理消息不该被主代理的工具结果污染）。 */
function lastMainIndex(list: UiMessage[]): number {
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const m = list[i]
    if (m.role === 'assistant' && !m.speaker) return i
  }
  return -1
}

/** 找某个子代理房间里的最后一条气泡（同一个子代理可能被委派多次）。 */
function lastSubIndex(
  list: UiMessage[],
  roomId: string,
  subagentId: string,
): number {
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const m = list[i]
    if (m.speaker?.kind === 'sub' && m.roomId === roomId && m.speaker.id === subagentId) {
      return i
    }
  }
  return -1
}

function newMainMessage(): UiMessage {
  return {
    id: `tmp-${Date.now()}`,
    role: 'assistant',
    text: '',
    toolCalls: [],
  }
}

interface ToolCallCard {
  id: string
  name: string
  input: Record<string, unknown>
  ok?: boolean
  output?: string
  /** 耗时（毫秒）。实时帧与落库记录都带，历史工具卡也能显示。 */
  ms?: number
}

// ---------------------------------------------------------------------------
// 附件 / 超长文本保护
//
// 背景：旧版「上传图片」是把文件读成 base64 直接塞进输入框的。一张手机照片
// 5MB → base64 约 6.8MB，一个几百万字符的**单行**放进受控 <textarea>，浏览器
// 排版直接把主线程占满 —— 用户看到的就是「页面无响应」。同理，消息气泡里若有
// 这种单行，也会卡死。
//
// 现在改成：图片先上传到工作区，消息里只留路径（几十个字符），字节永不进
// 上下文；同时下面的兜底会剥掉/截断任何残留的超长内容。
// ---------------------------------------------------------------------------

/** 输入框内容上限（字符）。超过必然异常，直接截断。 */
const MAX_DRAFT_CHARS = 200_000
/** 单行渲染上限 —— 超过就当二进制垃圾处理。 */
const MAX_RENDER_LINE = 4_000

/** 剥掉内联 base64 图片/数据，只留一句说明。避免拖死输入框排版。 */
function stripInlineData(text: string): string {
  if (!text.includes('data:')) return text
  return text.replace(
    /data:[\w.+-]+\/[\w.+-]+;base64,[A-Za-z0-9+/=\s]+/g,
    (m) => `[已丢弃内联 base64 数据 · ${Math.round(m.length / 1024)}KB]`,
  )
}

/** 单行截断：模型/历史里可能已经躺着几 MB 的乱码，渲染时保护一下。 */
function clampLine(line: string): string {
  if (line.length <= MAX_RENDER_LINE) return line
  return `${line.slice(0, MAX_RENDER_LINE)}…（本行共 ${line.length} 字符，已截断）`
}

/** ``![alt](src)`` —— 用户消息里的图片附件引用。 */
const IMAGE_REF_RE = /!\[([^\]]*)\]\(([^)\s]+)\)/g
/** ``[附件: name](src)`` —— 非图片附件引用。 */
const FILE_REF_RE = /\[附件[:：]\s*([^\]]*)\]\(([^)\s]+)\)/g

interface ImageRef {
  alt: string
  src: string
  /** 可直接预览的 URL（附件走 /api/upload/raw；远程 http 地址原样用）。 */
  url?: string
}

interface FileRef {
  name: string
  src: string
}

/**
 * 图片引用 → 可直接加载的 URL。
 * * 远程 http(s) 原样用；
 * * 本地绝对路径（`C:\…` / `/…`）走 `/api/fs/raw` —— agent **生成**到 workspace
 *   任意位置的图（image/、generated/ 等）不在 uploads，`upload/raw` 按存储名取不到；
 * * 其余按上传存储名走 `/api/upload/raw`。
 * * `data:` 内联不预览（体积大且没意义）。
 */
function imageUrlFor(src: string): string | undefined {
  if (/^https?:\/\//.test(src)) return src
  if (src.startsWith('data:')) return undefined
  if (/^[A-Za-z]:[\\/]/.test(src) || src.startsWith('/')) {
    return `/api/fs/raw?path=${encodeURIComponent(src)}`
  }
  const base = src.split(/[\\/]/).pop()
  return base ? `/api/upload/raw?name=${encodeURIComponent(base)}` : undefined
}

/** 把附件引用从正文里摘出来，正文只留用户真正打的字。 */
function splitAttachments(text: string): {
  text: string
  images: ImageRef[]
  files: FileRef[]
} {
  const images: ImageRef[] = []
  const toRef = (alt: string, src: string): ImageRef => {
    const ref: ImageRef = { alt: alt || '图片', src }
    const url = imageUrlFor(src)
    if (url) ref.url = url
    return ref
  }
  let out = text.replace(IMAGE_REF_RE, (_m, alt: string, src: string) => {
    images.push(toRef(alt, src))
    return ''
  })
  const files: FileRef[] = []
  out = out.replace(FILE_REF_RE, (_m, name: string, src: string) => {
    files.push({ name: name || '附件', src })
    return ''
  })
  return { text: out.replace(/\n{3,}/g, '\n\n').trim(), images, files }
}

/**
 * ChatView is now JUST the main chat area (topbar + scroll + composer +
 * collapsible right panel). Session / workspace management lives in the
 * shared Sidebar — App.tsx holds the active session id.
 */
export function ChatView({
  activeSessionId,
  onChangeSession,
}: ChatViewProps) {
  // sessions list (for the small "switch" chip + the new-session button on top)
  const [sessions, setSessions] = useState<ChatSessionInfo[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  // ── 多会话并行 ──────────────────────────────────────────────────────────
  // 消息、运行态、排队都按**会话**分桶：A 会话在跑的时候切到 B，B 照常发消息、
  // 照常跑，两边互不干扰（后端本来就按会话 id 隔离，见 _RUNNING_CHATS）。
  const [msgsBySid, setMsgsBySid] = useState<Record<string, UiMessage[]>>({})
  /** 与 msgsBySid 同步的权威副本：不触发重渲，用来判断「这个会话加载过没有」。 */
  const msgsRef = useRef<Record<string, UiMessage[]>>({})
  const [runningSids, setRunningSids] = useState<string[]>([])
  const runningRef = useRef<Set<string>>(new Set())
  /** 生成中还可以继续输入：这些消息先按会话排队，本轮结束后自动发下一条。 */
  const queuesRef = useRef<Record<string, string[]>>({})
  const [queues, setQueues] = useState<Record<string, string[]>>({})
  const messages =
    (activeSessionId ? msgsBySid[activeSessionId] : undefined) ?? EMPTY_MESSAGES
  const busy = !!activeSessionId && runningSids.includes(activeSessionId)
  const queuedCount = activeSessionId
    ? (queues[activeSessionId]?.length ?? 0)
    : 0
  // 「暂停」后的提示与错误也按会话记，免得 A 会话的报错挂在 B 的界面上。
  const [stoppedBySid, setStoppedBySid] = useState<Record<string, boolean>>({})
  const [errorsBySid, setErrorsBySid] = useState<Record<string, string | null>>({})
  const stoppedNote = !!stoppedBySid[activeSessionId ?? GLOBAL_SID]
  const error = errorsBySid[activeSessionId ?? GLOBAL_SID] ?? null

  /** 改某个会话的消息列表（帧一律走它，不再直接动"当前会话"）。 */
  const patchMsgs = useCallback(
    (sid: string, fn: (prev: UiMessage[]) => UiMessage[]) => {
      const next = { ...msgsRef.current, [sid]: fn(msgsRef.current[sid] ?? []) }
      msgsRef.current = next
      setMsgsBySid(next)
    },
    [],
  )

  /** 标记某个会话「在跑 / 跑完」，并广播给侧边栏。 */
  const markRunning = useCallback((sid: string, on: boolean) => {
    const next = new Set(runningRef.current)
    if (on) next.add(sid)
    else next.delete(sid)
    runningRef.current = next
    const list = [...next]
    setRunningSids(list)
    window.dispatchEvent(new CustomEvent(RUNNING_EVENT, { detail: list }))
  }, [])

  /** 改某个会话的排队列表。 */
  const patchQueue = useCallback((sid: string, fn: (prev: string[]) => string[]) => {
    const cur = queuesRef.current[sid] ?? []
    queuesRef.current = { ...queuesRef.current, [sid]: fn(cur) }
    setQueues({ ...queuesRef.current })
  }, [])

  /** 写错误/暂停提示：不传 sid 就落到当前会话（新建会话失败等场景落全局桶）。 */
  const setError = useCallback((msg: string | null, sid?: string) => {
    const key = sid ?? activeIdRef.current ?? GLOBAL_SID
    setErrorsBySid((prev) => ({ ...prev, [key]: msg }))
  }, [])
  const setStoppedNote = useCallback((on: boolean, sid?: string) => {
    const key = sid ?? activeIdRef.current ?? GLOBAL_SID
    setStoppedBySid((prev) => ({ ...prev, [key]: on }))
  }, [])

  // 广播"谁在跑"给侧边栏（会话行上的运行中小圆点）。卸载时清空。
  useEffect(() => {
    return () => {
      window.dispatchEvent(new CustomEvent(RUNNING_EVENT, { detail: [] }))
    }
  }, [])
  const [configWarning, setConfigWarning] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  // 手机竖屏一律先收起（300px 固定宽会把聊天区压成一条缝）；其余情况用记住的
  // 偏好，默认展开。展开后随时能点顶栏/右缘的按钮再打开。
  const [rightOpen, setRightOpen] = useState(
    () => !phonePortrait() && localStorage.getItem(RIGHT_OPEN_KEY) !== '0',
  )
  /** 切右侧工具栏（点按钮/点面板里的关闭）。 */
  const toggleRight = useCallback((next?: boolean) => {
    setRightOpen((v) => {
      const nv = next ?? !v
      localStorage.setItem(RIGHT_OPEN_KEY, nv ? '1' : '0')
      return nv
    })
  }, [])

  // 转成手机竖屏（横屏→竖屏、或者把窗口拉窄）→ 右侧工具栏收起，
  // 不然它的固定宽度会立刻把聊天区挤成一条缝。
  useEffect(() => {
    const mq = window.matchMedia(PHONE_PORTRAIT)
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setRightOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  // Agent 循环模式：react=增强版（同参重复立刻拦 + 空转自动收尾）/ kt=KT 原版。
  // 存在 settings.agent.loopMode，聊天页顶部可直接切换（下一轮对话生效）。
  const [loopMode, setLoopMode] = useState<'react' | 'kt'>('react')

  // ── 群聊 ────────────────────────────────────────────────────────────────
  // 主代理与被拉进群的子代理在同一个聊天框里说话：子代理的发言、它自己调的
  // 工具都由后端的 subagent* 系列帧推来。`seen` 是**实际出现过的**子代理
  // （比名单更真实 —— 模型自己委派出去的也算），名单则是用户手动拉进来的。
  const [roster, setRoster] = useState<GroupMember[]>([])
  // 实际出现过的子代理也按会话分桶（并行时 A 会话的子代理不该出现在 B 的好友栏）。
  const [seenBySid, setSeenBySid] = useState<Record<string, Speaker[]>>({})
  const seen = (activeSessionId ? seenBySid[activeSessionId] : undefined) ?? EMPTY_SPEAKERS
  const [candidates, setCandidates] = useState<SubagentInfo[]>([])
  const [groupOpen, setGroupOpen] = useState(false)
  /** 「拉成员进群」浮层里新建真人席位的名字输入。 */
  const [humanName, setHumanName] = useState('')
  const [showSub, setShowSub] = useState(true)
  /** 「分配项目」：成员 id → 工作空间 id（子代理各写各的项目目录）。 */
  const [memberProjects, setMemberProjects] = useState<Record<string, string>>({})
  const [projectOpen, setProjectOpen] = useState(false)
  const memberProjectsRef = useRef<Record<string, string>>({})
  const projectRef = useRef<HTMLDivElement | null>(null)
  const groupRef = useRef<HTMLDivElement | null>(null)
  const rosterRef = useRef<GroupMember[]>([])
  const seenRef = useRef<Record<string, Speaker[]>>({})
  // 某个会话里新露面的子代理（按会话记，换会话不用清 —— 各自独立）。
  const rememberSub = useCallback((sid: string, sp: Speaker) => {
    const cur = seenRef.current[sid] ?? []
    if (cur.some((s) => s.id === sp.id)) return
    const next = { ...seenRef.current, [sid]: [...cur, sp] }
    seenRef.current = next
    setSeenBySid(next)
  }, [])

  const socketRef = useRef<OpenSocketHandle | null>(null)
  const [socketState, setSocketState] = useState<SocketState>('connecting')
  const activeIdRef = useRef<string | null>(activeSessionId)
  const pickerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    activeIdRef.current = activeSessionId
  }, [activeSessionId])

  // 读一次循环模式（全局设置）；切换时写回，下一轮对话生效。
  useEffect(() => {
    let alive = true
    api
      .settingsGet()
      .then((s) => {
        if (alive && s.agent?.loopMode === 'kt') setLoopMode('kt')
      })
      .catch(() => {
        /* 读不到就用默认 react */
      })
    return () => {
      alive = false
    }
  }, [])

  const switchLoopMode = useCallback(async (mode: 'react' | 'kt') => {
    setLoopMode(mode)
    try {
      await api.settingsPut({ agent: { loopMode: mode } })
    } catch (e) {
      console.warn('loopMode save failed', e)
    }
  }, [])

  // -- 群聊：可拉进来的子代理清单（助理页配好的那些）-----------------------
  useEffect(() => {
    let alive = true
    api
      .subagentsList()
      .then((r) => {
        if (alive) setCandidates(r.subagents ?? [])
      })
      .catch(() => {
        /* 助理页还没配过子代理，或读不到 —— 群聊就只剩主代理 */
      })
    return () => {
      alive = false
    }
  }, [])

  // 子代理过程的展开开关（全局偏好，存本地）。
  useEffect(() => {
    try {
      const raw = localStorage.getItem(SHOW_SUB_KEY)
      if (raw === '0') setShowSub(false)
    } catch {
      /* 隐私模式 */
    }
  }, [])
  const toggleShowSub = useCallback(() => {
    setShowSub((v) => {
      const next = !v
      try {
        localStorage.setItem(SHOW_SUB_KEY, next ? '1' : '0')
      } catch {
        /* 忽略 */
      }
      return next
    })
  }, [])

  // 换会话 → 载入该会话的群成员名单与「分配项目」。
  // （子代理「露过面」的名单按会话独立存放，不必在这里清。）
  useEffect(() => {
    const ids = loadGroup(activeSessionId)
    rosterRef.current = ids
    setRoster(ids)
    const projects = loadMemberProjects(activeSessionId)
    memberProjectsRef.current = projects
    setMemberProjects(projects)
    setGroupOpen(false)
    setProjectOpen(false)
  }, [activeSessionId])

  /** 拉进 / 请出群成员。``kind`` 只是身份标签：agent / bot 都是子代理，
   *  human 是真人席位（只挂个名字，不干活）。 */
  const toggleMember = useCallback(
    (id: string, kind: GroupKind = 'agent', name?: string) => {
      const cur = rosterRef.current
      const exists = cur.some((m) => m.id === id)
      const next = exists
        ? cur.filter((m) => m.id !== id)
        : [...cur, { id, kind, name }]
      rosterRef.current = next
      setRoster(next)
      saveGroup(activeSessionId, next)
      // 请出群的人顺带清掉它的项目分配，免得下次拉回来还带着旧目录。
      if (exists) setMemberProject(id, '')
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeSessionId],
  )

  /** 改某个成员的身份标签（agent ⇄ bot）；若它还没进群，顺手拉进来。 */
  const setMemberKind = useCallback(
    (id: string, kind: GroupKind, name?: string) => {
      const cur = rosterRef.current
      const has = cur.some((m) => m.id === id)
      const next = has
        ? cur.map((m) => (m.id === id ? { ...m, kind } : m))
        : [...cur, { id, kind, name }]
      rosterRef.current = next
      setRoster(next)
      saveGroup(activeSessionId, next)
    },
    [activeSessionId],
  )

  /** 加一个真人席位进群（只是发言人，不新增任何可执行成员）。 */
  const addHumanSeat = useCallback(
    (rawName: string) => {
      const name = rawName.trim()
      if (!name) return
      const cur = rosterRef.current
      // 用自增序号做 id，避免同名冲突；名字存下来给系统提示用。
      let n = 1
      while (cur.some((m) => m.id === `human:${n}`)) n += 1
      const next = [...cur, { id: `human:${n}`, kind: 'human' as GroupKind, name }]
      rosterRef.current = next
      setRoster(next)
      saveGroup(activeSessionId, next)
    },
    [activeSessionId],
  )

  /** 给某个成员分配项目（``folderId`` 传空字符串 = 取消分配）。 */
  const setMemberProject = useCallback(
    (memberId: string, folderId: string) => {
      const next = { ...memberProjectsRef.current }
      if (folderId) next[memberId] = folderId
      else delete next[memberId]
      memberProjectsRef.current = next
      setMemberProjects(next)
      saveMemberProjects(activeSessionId, next)
    },
    [activeSessionId],
  )

  /** 群（会话）自己的项目 —— 就是会话所属的工作空间，改了立刻持久化。 */
  // （实现在 reloadSessions/reloadWorkspaces 之后 —— 它俩要先声明。）

  // 点开群成员浮层时，点空白处收起。
  useEffect(() => {
    if (!groupOpen) return
    const onDoc = (e: MouseEvent) => {
      if (groupRef.current && !groupRef.current.contains(e.target as Node)) {
        setGroupOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [groupOpen])

  // 项目浮层同理。
  useEffect(() => {
    if (!projectOpen) return
    const onDoc = (e: MouseEvent) => {
      if (projectRef.current && !projectRef.current.contains(e.target as Node)) {
        setProjectOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [projectOpen])

  // -- reload helpers --------------------------------------------------------
  const reloadSessions = useCallback(async () => {
    try {
      const data = await api.chatSessions()
      setSessions(data.sessions)
    } catch (e) {
      console.warn('sessions load failed', e)
    }
  }, [])

  const reloadWorkspaces = useCallback(async () => {
    try {
      const r = await api.workspaces()
      setWorkspaces(r.workspaces)
    } catch (e) {
      console.warn('workspaces load failed', e)
    }
  }, [])

  useEffect(() => {
    void reloadSessions()
    void reloadWorkspaces()
  }, [reloadSessions, reloadWorkspaces])

  /** 群（会话）自己的项目 —— 就是会话所属的工作空间，改了立刻持久化。
   *  落库后沙箱目录、右侧「项目文件」根都会跟着这个项目走。 */
  const setGroupProject = useCallback(
    async (folderId: string | null) => {
      if (!activeSessionId) return
      try {
        await api.chatMove(activeSessionId, folderId)
        await reloadSessions()
        await reloadWorkspaces()
        setProjectOpen(false)
      } catch (e) {
        setError(String((e as Error).message))
      }
    },
    [activeSessionId, reloadSessions, reloadWorkspaces],
  )

  // -- load messages when active session changes ----------------------------
  // 并行时别的会话可能正在后台流式输出；切回来不能把本地攒下的内容冲掉，
  // 所以「加载过就保留本地」—— 本地那份还带着工具卡与子代理过程，比历史更全。
  // ``force`` 用于重连后对齐：那会儿本地流式状态已经不可信了，以服务端为准。
  const loadSessionMessages = useCallback(
    async (sid: string, force = false) => {
      if (!force && msgsRef.current[sid]) return
      try {
        const data = await api.chatMessages(sid)
        if (!force && msgsRef.current[sid]) return
        patchMsgs(sid, () =>
          data.messages.flatMap<UiMessage>((m: ChatMessageInfo) => {
            // [T-tool-cards-persist-and-fold] 工具卡随消息一起落库了 —— 把
            // 它们画回来（默认折叠，点开看调用参数与输出）。这些内容不进
            // 模型上下文，只服务于「刚才到底干了什么」这件事。
            const runs = (m.runs ?? []).map<ToolCallCard>((r) => ({
              id: r.id,
              name: r.name,
              input: r.input ?? {},
              ok: r.ok,
              output: r.output,
              ms: r.ms,
            }))
            // [T-subagent-log-persist] 子代理那段过程也落库了：重建发言人
            // 与它的工具卡。原先只有实时帧里有，切窗口/刷新就整段消失 ——
            // 开着子代理时，绝大部分工具调用其实发生在子代理里。
            if (m.sub) {
              const sp: Speaker = {
                id: m.sub.speaker?.subagentId || m.sub.speaker?.id || 'sub',
                name: m.sub.speaker?.name || '子代理',
                emoji: m.sub.speaker?.emoji || '🤖',
                kind: 'sub',
                project: m.sub.speaker?.project || undefined,
              }
              rememberSub(sid, sp)
              return [
                {
                  id: m.id,
                  role: 'assistant' as const,
                  text: m.sub.text ?? '',
                  speaker: sp,
                  roomId: m.sub.roomId || undefined,
                  subTask: m.sub.task || undefined,
                  subTools: runs,
                  subDone: true,
                },
              ]
            }
            return [
              { id: m.id, role: m.role, text: m.text, toolCalls: runs },
            ]
          }),
        )
      } catch (e) {
        setError(String((e as Error).message), sid)
      }
    },
    [patchMsgs, rememberSub, setError],
  )

  useEffect(() => {
    activeIdRef.current = activeSessionId
    if (!activeSessionId) return
    void loadSessionMessages(activeSessionId)
  }, [activeSessionId, loadSessionMessages])

  // -- 发送 / 排队 / 暂停 --------------------------------------------------
  const dispatch = useCallback(
    (text: string, sid: string) => {
      const ws = socketRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        // 消息留在界面上、会话标成没在跑 —— 代理会把这帧排队，等重连后再发；
        // 顶部那颗状态小药丸负责提示。
        setError('连接已断开,正在重连…', sid)
        markRunning(sid, false)
        return
      }
      markRunning(sid, true)
      // 把「群成员」随消息一起带上：后端据此把它们写进系统提示，主代理才知道
      // 群里有这些同事、可以委派给谁。
      ws.send(
        JSON.stringify({
          type: 'chat',
          text,
          sessionId: sid,
          participants: rosterRef.current,
          // 「分配项目」：成员 → 工作空间 id（后端解析成目录当它的工作目录）。
          memberProjects: memberProjectsRef.current,
        }),
      )
    },
    [markRunning, setError],
  )

  /** 某个会话本轮结束后，把它排队的第一条发出去（一次一条，发完再等 done）。 */
  const flushQueue = useCallback(
    (sid: string) => {
      const next = (queuesRef.current[sid] ?? []).shift()
      patchQueue(sid, (prev) => prev.slice(1))
      if (next === undefined) return
      dispatch(next, sid)
    },
    [dispatch, patchQueue],
  )

  /** 「暂停」：中断本轮生成（已排队/已输入内容都不受影响）。 */
  const stop = useCallback(() => {
    const sid = activeIdRef.current
    const ws = socketRef.current
    if (!sid || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'stop', sessionId: sid }))
  }, [])

  // -- one shared websocket ------------------------------------------------
  const handleFrame = useCallback(
    (frame: SessionScopedFrame) => {
      // 帧自带会话 id（多会话并行就这么路由回各自的列表）；没有就退回
      // "当前打开的会话" —— 老后端或非会话流（如 shell）走到这条分支。
      const sid = frame.sessionId ?? activeIdRef.current

      switch (frame.type) {
        case 'chatSession':
          activeIdRef.current = frame.sessionId
          onChangeSession(frame.sessionId)
          break
        case 'delta': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            let tail = list[list.length - 1]
            // 子代理刚发过言时，最后一条是**它**的气泡 —— 主代理的下一段文字
            // 要另起一条，群聊才读得顺（否则会追到子代理气泡上）。
            if (!tail || tail.role !== 'assistant' || tail.speaker) {
              tail = newMainMessage()
              list.push(tail)
            }
            list[list.length - 1] = {
              ...tail,
              text: tail.text + frame.text,
            }
            return list
          })
          break
        }
        case 'toolStart': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            let i = lastMainIndex(list)
            if (i < 0) {
              list.push(newMainMessage())
              i = list.length - 1
            }
            const tail = list[i]
            list[i] = {
              ...tail,
              toolCalls: [
                ...(tail.toolCalls ?? []),
                { id: frame.id, name: frame.name, input: frame.input ?? {} },
              ],
            }
            return list
          })
          break
        }
        case 'toolEnd': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            const i = lastMainIndex(list)
            if (i < 0) return list
            const tail = list[i]
            const cards = (tail.toolCalls ?? []).map((c) =>
              c.id === frame.id
                ? { ...c, ok: frame.ok, output: frame.output, ms: frame.ms }
                : c,
            )
            // 生图自动预览：后端把本次新生成的图片路径带在 toolEnd 上，
            // 直接挂到这条 assistant 消息（去重），无需模型写 markdown。
            const incoming = frame.images ?? []
            const generated = incoming.length
              ? Array.from(new Set([...(tail.generated ?? []), ...incoming]))
              : tail.generated
            list[i] = { ...tail, toolCalls: cards, generated }
            return list
          })
          break
        }
        // ── 群聊：子代理开麦 ───────────────────────────────────────────
        case 'subagentStart': {
          if (!sid) break
          const sp: Speaker = {
            id: frame.subagentId,
            name: frame.name || frame.subagentId,
            emoji: frame.emoji || '🤖',
            kind: 'sub',
            project: frame.project || undefined,
          }
          rememberSub(sid, sp)
          patchMsgs(sid, (prev) => [
            ...prev,
            {
              id: `sub-${frame.id}-${frame.subagentId}`,
              role: 'assistant',
              text: '',
              speaker: sp,
              roomId: frame.id,
              subTask: frame.task,
              subTools: [],
            },
          ])
          break
        }
        case 'subagentDelta': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            const i = lastSubIndex(list, frame.id, frame.subagentId)
            if (i < 0) return list
            list[i] = { ...list[i], text: list[i].text + frame.text }
            return list
          })
          break
        }
        case 'subagentToolStart': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            const i = lastSubIndex(list, frame.id, frame.subagentId)
            if (i < 0) return list
            const cur = list[i]
            list[i] = {
              ...cur,
              subTools: [
                ...(cur.subTools ?? []),
                { id: frame.callId, name: frame.name, input: frame.input ?? {} },
              ],
            }
            return list
          })
          break
        }
        case 'subagentToolEnd': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            const i = lastSubIndex(list, frame.id, frame.subagentId)
            if (i < 0) return list
            const cur = list[i]
            // 子代理自己生的图也自动预览（脚本只打印文件名，模型常忘记写链接）
            const incoming = frame.images ?? []
            list[i] = {
              ...cur,
              subTools: (cur.subTools ?? []).map((c) =>
                c.id === frame.callId
                  ? { ...c, ok: frame.ok, output: frame.output }
                  : c,
              ),
              generated: incoming.length
                ? Array.from(new Set([...(cur.generated ?? []), ...incoming]))
                : cur.generated,
            }
            return list
          })
          break
        }
        case 'subagentEnd': {
          if (!sid) break
          patchMsgs(sid, (prev) => {
            const list = [...prev]
            for (let i = list.length - 1; i >= 0; i -= 1) {
              const m = list[i]
              if (m.roomId !== frame.id || m.speaker?.id !== frame.subagentId)
                continue
              // 流式没接到文字（网关只给最终文本）时用收尾文本兜底。
              const text = m.text.trim() ? m.text : frame.text || m.text
              list[i] = { ...m, text, subDone: true }
              break
            }
            return list
          })
          break
        }
        case 'done': {
          if (!sid) break
          markRunning(sid, false)
          if (frame.stopped) setStoppedNote(true, sid)
          if ((queuesRef.current[sid] ?? []).length > 0) {
            // 还有排队的消息 → 直接接着发（消息已在界面上，不必先刷历史）
            flushQueue(sid)
          } else {
            void reloadSessions()
          }
          break
        }
        case 'fallback': {
          if (!sid) break
          // 主模型被限流/超时，后端已切到兜底模型 —— 留一条提示，
          // 免得用户看到「回复突然换了个语气」却不知道发生了什么。
          patchMsgs(sid, (prev) => [
            ...prev,
            {
              id: `fb-${Date.now()}-${frame.toModel}`,
              role: 'assistant',
              text: '',
              fallbackNote:
                `⚠️ ${frame.fromModel} 不可用（${frame.reason}）` +
                ` → 已自动切换到 ${frame.toModel}`,
            },
          ])
          break
        }
        case 'error':
          if (sid) {
            setError(frame.error, sid)
            markRunning(sid, false)
            if ((queuesRef.current[sid] ?? []).length > 0) flushQueue(sid)
          }
          break
        default:
          break
      }
    },
    [
      flushQueue,
      markRunning,
      onChangeSession,
      patchMsgs,
      reloadSessions,
      rememberSub,
      setError,
      setStoppedNote,
    ],
  )

  /** 断线重连之后的对齐。
   *
   *  流式帧只发给"当时那条连接"，断线期间的输出收不回来了 —— 所以：
   *  1) 清掉「在跑」标记（否则界面永远卡在生成中，连暂停按钮都是死的）；
   *  2) 丢掉这些会话的本地缓存，改为重新拉服务端历史 —— 本轮若已落库，
   *     回答会补回来（这也是以前"只能刷新页面"才能恢复的原因）；
   *  3) 把断线时排队没发出去的消息补发，用户不用再打一遍。
   */
  const resyncAfterReconnect = useCallback(async () => {
    const wasRunning = [...runningRef.current]
    for (const sid of wasRunning) markRunning(sid, false)
    if (wasRunning.length > 0) {
      const next = { ...msgsRef.current }
      for (const sid of wasRunning) delete next[sid]
      msgsRef.current = next
      setMsgsBySid(next)
    }
    await reloadSessions()
    const sid = activeIdRef.current
    if (sid) await loadSessionMessages(sid, true)
    const queued = Object.entries(queuesRef.current).filter(
      ([, list]) => list.length > 0,
    )
    for (const [qsid, list] of queued) {
      patchQueue(qsid, () => [])
      for (const text of list) dispatch(text, qsid)
    }
  }, [
    dispatch,
    loadSessionMessages,
    markRunning,
    patchQueue,
    reloadSessions,
  ])

  useEffect(() => {
    const ws = openSocket(handleFrame, {
      onStateChange: setSocketState,
      // 重连成功 → 拉一次真实状态。断线期间的流收不回来，不这么做就会卡在
      // 「生成中」，用户只能刷新页面（老 bug）。
      onReconnect: () => void resyncAfterReconnect(),
    })
    socketRef.current = ws
    return () => {
      ws.close()
      socketRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleFrame])

  // -- close picker when clicking outside -----------------------------------
  useEffect(() => {
    if (!pickerOpen) return
    const onDoc = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [pickerOpen])

  // -- actions --------------------------------------------------------------
  const send = useCallback(
    (text: string) => {
      const t = text.trim()
      if (!t) return
      const sid = activeIdRef.current
      if (!sid) return
      setError(null, sid)
      setConfigWarning(null)
      setStoppedNote(false, sid)
      patchMsgs(sid, (prev) => [
        ...prev,
        { id: `local-${Date.now()}`, role: 'user', text: t, toolCalls: [] },
      ])
      if (runningRef.current.has(sid)) {
        // 本会话生成中 → 先排队，本轮 done 后自动发出（消息已显示在界面上）。
        // 注意只看**本会话**在不在跑：别的会话在跑不影响这里，这正是并行。
        patchQueue(sid, (prev) => [...prev, t])
        return
      }
      dispatch(t, sid)
    },
    [dispatch, patchMsgs, patchQueue, setError, setStoppedNote],
  )

  const createChatHere = useCallback(
    async (folderId?: string | null) => {
      try {
        const created = await api.chatCreate(folderId ?? undefined)
        await reloadSessions()
        onChangeSession(created.id)
        patchMsgs(created.id, () => [])
        setError(null)
        setPickerOpen(false)
      } catch (e) {
        setError(String((e as Error).message))
      }
    },
    [onChangeSession, patchMsgs, reloadSessions, setError],
  )

  // -- clear current session messages ---------------------------------------
  const clearSession = useCallback(() => {
    if (!activeSessionId) return
    patchMsgs(activeSessionId, () => [])
    setError(null)
  }, [activeSessionId, patchMsgs, setError])

  // -- derived --------------------------------------------------------------
  const activeSession =
    sessions.find((s) => s.id === activeSessionId) ?? null
  const activeWorkspace = useMemo(
    () =>
      activeSession?.folderId
        ? workspaces.find((w) => w.id === activeSession.folderId) ?? null
        : null,
    [activeSession, workspaces],
  )

  // 群成员条要显示的人：名单里的（用户拉进来的）+ 本轮实际出现过的
  // （模型自己委派出去的也算 —— 否则「群里冒出一个陌生名字」）。
  // 每一条都带上「身份标签」（agent / bot / 人）用于分组显示 —— 只是标签，
  // 不改谁能干活：真人席位永远只是发言人。
  interface GroupChip {
    id: string
    name: string
    emoji: string
    groupKind: GroupKind
    /** 本轮实际开过麦（子代理）。 */
    active: boolean
    /** 在名单里（可请出群）；模型临时委派的成员不在此列。 */
    inRoster: boolean
  }
  const groupChips = useMemo<GroupChip[]>(() => {
    const out: GroupChip[] = []
    for (const m of roster) {
      if (m.kind === 'human') {
        out.push({
          id: m.id,
          name: m.name || '真人',
          emoji: GROUP_KIND_ICON.human,
          groupKind: 'human',
          active: false,
          inRoster: true,
        })
        continue
      }
      const cfg = candidates.find((c) => c.id === m.id)
      out.push({
        id: m.id,
        name: cfg?.name || m.id,
        emoji: cfg?.emoji || (m.kind === 'bot' ? GROUP_KIND_ICON.bot : '🤖'),
        groupKind: m.kind,
        active: seen.some((s) => s.id === m.id),
        inRoster: true,
      })
    }
    for (const sp of seen) {
      if (out.some((c) => c.id === sp.id)) continue
      out.push({
        id: sp.id,
        name: sp.name,
        emoji: sp.emoji,
        groupKind: 'agent',
        active: true,
        inRoster: false,
      })
    }
    return out
  }, [roster, candidates, seen])

  /** 名单里的真人席位（只是发言人）。 */
  const humanSeats = useMemo(
    () => roster.filter((m) => m.kind === 'human'),
    [roster],
  )

  // 折叠子代理过程时只把它们的发言/工具摘出去 —— `messages` 本身不动，
  // 主代理的流式拼接不受影响。
  const visibleMessages = useMemo(
    () => (showSub ? messages : messages.filter((m) => m.speaker?.kind !== 'sub')),
    [messages, showSub],
  )

  // -- render --------------------------------------------------------------
  return (
    <div className="view chat chat-with-rail">
      {/* 手机：右栏收起后的展开把手（桌面端由 CSS 隐藏）。折叠后总得有
          个看得见、够得到的按钮，不然只能靠猜「从边上滑出来」。 */}
      {!rightOpen && (
        <button
          type="button"
          className="mobile-toolbar-btn"
          title="展开工具栏（项目文件 / 历史提问）"
          aria-label="展开工具栏"
          onClick={() => toggleRight(true)}
        >
          ❮
        </button>
      )}
      <section className="chat-main">
        <div className="chat-topbar">
          <div className="picker" ref={pickerRef}>
            <button
              className="picker-trigger"
              onClick={() => setPickerOpen((v) => !v)}
              title="切换会话"
            >
              <span className="picker-label">
                {activeSession
                  ? activeSession.title
                  : '新建一个会话开始对话'}
              </span>
              <span className="picker-caret">▾</span>
            </button>
            {pickerOpen && (
              <div className="picker-pop">
                <button
                  className="picker-action"
                  onClick={() => void createChatHere(null)}
                >
                  ＋ 新会话
                </button>
                <div className="picker-divider" />
                <ul className="picker-list">
                  {sessions.length === 0 && (
                    <li className="muted empty-line">尚无会话</li>
                  )}
                  {sessions.map((s) => (
                    <li
                      key={s.id}
                      className={
                        activeSessionId === s.id ? 'active' : ''
                      }
                      onClick={() => {
                        onChangeSession(s.id)
                        setError(null)
                        setPickerOpen(false)
                      }}
                    >
                      <span className="picker-title">{s.title}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {activeWorkspace && (
            <span
              className="topbar-ws-tag"
              title={
                activeWorkspace.path
                  ? `电脑目录: ${activeWorkspace.path}`
                  : '当前会话所属工作空间'
              }
            >
              📁 {activeWorkspace.name}
            </span>
          )}

          <div className="topbar-spacer" />

          <div
            className="cap-picker loop-mode-picker"
            title="Agent 循环模式：ReAct 增强版会在第一次重复调用时立刻停下；KT 原版只跑移植的四条策略（10/20/30）"
          >
            <button
              type="button"
              className={`chip cap-chip cap-llm ${loopMode === 'react' ? 'on' : ''}`}
              onClick={() => void switchLoopMode('react')}
            >
              ReAct
            </button>
            <button
              type="button"
              className={`chip cap-chip cap-vision ${loopMode === 'kt' ? 'on' : ''}`}
              onClick={() => void switchLoopMode('kt')}
            >
              原版
            </button>
          </div>

          <button
            className="rp-toggle-edge"
            title={rightOpen ? '收起右侧栏' : '展开右侧栏'}
            onClick={() => toggleRight()}
          >
            {rightOpen ? (
              <>
                收起工具栏 <b className="rp-arrow">❯</b>
              </>
            ) : (
              <>
                <b className="rp-arrow">❮</b> 展开工具栏
              </>
            )}
          </button>
        </div>

        {/* 群聊成员条：主代理 + 被拉进群的成员。**只显示头像/图标**（名字放
            tooltip），身份用颜色 + 角标区分，免得占地方。 */}
        <div className="chat-groupbar" ref={groupRef}>
          <div className="group-members">
            <span className="group-chip is-user" title="你">
              <span className="group-ava">{USER_SPEAKER.emoji}</span>
            </span>
            <span className="group-chip is-main" title="主代理（替你和子代理对接）">
              <span className="group-ava">{MAIN_SPEAKER.emoji}</span>
            </span>
            {groupChips.map((m) => {
              // 真人席位不干活，没有「分配项目」这一说。
              const projId = m.groupKind === 'human' ? '' : memberProjects[m.id]
              const proj = projId
                ? workspaces.find((w) => w.id === projId)
                : undefined
              const kindLabel = GROUP_KIND_LABEL[m.groupKind]
              return (
                <span
                  key={m.id}
                  className={`group-chip is-sub is-kind-${m.groupKind}${
                    m.active ? ' is-active' : ''
                  }`}
                  title={
                    m.groupKind === 'human'
                      ? `${m.name}（人 · 真人席位，只是发言人，不干活）`
                      : m.active
                        ? `${m.name}（${kindLabel} · 本轮已参与）`
                        : `${m.name}（${kindLabel} · 已拉进群，还没被委派）`
                  }
                >
                  <span className="group-ava">
                    {m.emoji}
                    {/* 身份角标：Agent 无点、Bot 蓝点、人 橙点（图不看名字也认得出）。 */}
                    {m.groupKind !== 'agent' && (
                      <i className={`group-dot is-${m.groupKind}`} />
                    )}
                  </span>
                  {proj && (
                    <span className="group-proj-tag" title={proj.path || '沙箱目录'}>
                      📁
                    </span>
                  )}
                  <button
                    type="button"
                    className={`group-x${m.inRoster ? '' : ' is-add'}`}
                    title={m.inRoster ? '请出群' : '拉进群'}
                    onClick={() =>
                      toggleMember(m.id, m.groupKind === 'human' ? 'human' : 'agent', m.name)
                    }
                  >
                    {m.inRoster ? '×' : '＋'}
                  </button>
                </span>
              )
            })}
          </div>

          <div className="group-actions">
            <div className="group-picker" ref={projectRef}>
              <button
                type="button"
                className="group-add group-proj"
                onClick={() => setProjectOpen((v) => !v)}
                title="给整个群指定工作项目（主代理的目录）；成员可在「拉子代理进群」里单独分配"
              >
                📁 项目：{activeWorkspace ? activeWorkspace.name : '未指定'}
              </button>
              {projectOpen && (
                <div className="group-pop">
                  <div className="group-pop-title">群项目 —— 主代理的工作目录</div>
                  <ul className="group-pop-list">
                    <li
                      className={!activeSession?.folderId ? 'on' : ''}
                      onClick={() => void setGroupProject(null)}
                    >
                      <span className="group-ava">🌐</span>
                      <span className="group-pop-name">不指定（用默认沙箱目录）</span>
                      <span className="group-pop-check">
                        {!activeSession?.folderId ? '✓' : ''}
                      </span>
                    </li>
                    {workspaces.map((w) => (
                      <li
                        key={w.id}
                        className={activeSession?.folderId === w.id ? 'on' : ''}
                        onClick={() => void setGroupProject(w.id)}
                        title={w.path || '未绑定电脑目录（用沙箱目录）'}
                      >
                        <span className="group-ava">📁</span>
                        <span className="group-pop-name">
                          {w.name}
                          {w.path && (
                            <span className="group-pop-id">{w.path}</span>
                          )}
                        </span>
                        <span className="group-pop-check">
                          {activeSession?.folderId === w.id ? '✓' : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {workspaces.length === 0 && (
                    <div className="muted empty-line">
                      还没有工作空间 —— 先去「工作空间」页建一个
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="group-picker">
              <button
                type="button"
                className="group-add"
                onClick={() => setGroupOpen((v) => !v)}
                title="拉成员进群：Agent / Bot（子代理）或真人席位"
              >
                ＋ 拉成员进群
              </button>
              {groupOpen && (
                <div className="group-pop">
                  <div className="group-pop-title">
                    拉成员进群 —— Agent / Bot / 人
                  </div>
                  <div className="group-pop-legend">
                    身份只是标签：
                    <span className="group-kind-tag is-agent">Agent</span>
                    <span className="group-kind-tag is-bot">Bot</span>
                    都是子代理，可被委派；
                    <span className="group-kind-tag is-human">人</span>
                    只作为发言人进群，不干活。
                  </div>
                  {candidates.length === 0 && (
                    <div className="muted empty-line">
                      还没有子代理 —— 先去「助理」页创建一个
                    </div>
                  )}
                  <ul className="group-pop-list">
                    {candidates.map((c) => {
                      const entry = roster.find((m) => m.id === c.id)
                      const kind: GroupKind =
                        entry && entry.kind !== 'human' ? entry.kind : 'agent'
                      const label = c.name || c.id
                      return (
                        <li key={c.id} className={entry ? 'on' : ''}>
                          <span
                            className="group-pop-hit"
                            onClick={() => toggleMember(c.id, kind, label)}
                          >
                            <span className="group-ava">{c.emoji || '🤖'}</span>
                            <span className="group-pop-name">
                              {c.name}
                              <span className="group-pop-id">{c.id}</span>
                            </span>
                          </span>
                          <select
                            className="group-pop-kind"
                            value={kind}
                            title="身份标签：Agent 或 Bot（都还是子代理）"
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) =>
                              setMemberKind(c.id, e.target.value as GroupKind, label)
                            }
                          >
                            <option value="agent">🧭 Agent</option>
                            <option value="bot">⚙️ Bot</option>
                          </select>
                          {entry && workspaces.length > 0 && (
                            <select
                              className="group-pop-proj"
                              value={memberProjects[c.id] ?? ''}
                              title="给这个成员分配项目目录"
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) =>
                                setMemberProject(c.id, e.target.value)
                              }
                            >
                              <option value="">跟随群项目</option>
                              {workspaces.map((w) => (
                                <option key={w.id} value={w.id}>
                                  📁 {w.name}
                                </option>
                              ))}
                            </select>
                          )}
                          <span
                            className="group-pop-check"
                            onClick={() => toggleMember(c.id, kind, label)}
                          >
                            {entry ? '✓' : '＋'}
                          </span>
                        </li>
                      )
                    })}
                  </ul>

                  <div className="group-pop-sep">真人席位</div>
                  <div className="group-pop-humanadd">
                    <input
                      value={humanName}
                      placeholder="名字，如「产品经理」"
                      onChange={(e) => setHumanName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          addHumanSeat(humanName)
                          setHumanName('')
                        }
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        addHumanSeat(humanName)
                        setHumanName('')
                      }}
                    >
                      添加
                    </button>
                  </div>
                  {humanSeats.length > 0 && (
                    <ul className="group-pop-list">
                      {humanSeats.map((m) => (
                        <li key={m.id} className="on">
                          <span
                            className="group-pop-hit"
                            onClick={() => toggleMember(m.id, 'human', m.name)}
                          >
                            <span className="group-ava">
                              {GROUP_KIND_ICON.human}
                            </span>
                            <span className="group-pop-name">
                              {m.name}
                              <span className="group-pop-id">{m.id}</span>
                            </span>
                          </span>
                          <span
                            className="group-pop-check"
                            onClick={() => toggleMember(m.id, 'human', m.name)}
                          >
                            ×
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
            <button
              type="button"
              className={`group-toggle${showSub ? ' on' : ''}`}
              onClick={toggleShowSub}
              title="显示/隐藏子代理的发言与工具过程"
            >
              {showSub ? '👥 子代理过程 显示中' : '👥 子代理过程 已折叠'}
            </button>
          </div>
        </div>

        {configWarning && (
          <div className="config-banner">
            <span>{configWarning}</span>
            <button onClick={() => (window.location.hash = '#settings')}>
              打开设置
            </button>
          </div>
        )}
        <MessageList
          messages={visibleMessages}
          sessionId={activeSessionId}
          showSub={showSub}
          onDeleted={(mid) => {
            if (!activeSessionId) return
            patchMsgs(activeSessionId, (prev) =>
              prev.filter((m) => m.id !== mid),
            )
          }}
        />
        {socketState !== 'open' && (
          <div className={`chat-sockpill chat-sockpill--${socketState}`}>
            {socketState === 'connecting' && '连接已断开,正在重连…'}
            {socketState === 'closing' && '正在关闭连接…'}
            {socketState === 'closed' && '连接已关闭'}
          </div>
        )}
        {error && <div className="chat-error">{error}</div>}
        {stoppedNote && (
          <div className="chat-stopped">⏹ 已暂停本轮生成（本轮内容未保存）</div>
        )}
        <Composer
          disabled={!activeSessionId}
          busy={busy}
          queued={queuedCount}
          onSend={send}
          onStop={stop}
          onClearSession={clearSession}
        />
        {!activeSessionId && (
          <div className="chat-hint">
            从左侧选择一个会话,或点击左上「＋ 新建任务」开始对话。
          </div>
        )}
      </section>

      <div className={`rp-col ${rightOpen ? '' : 'collapsed'}`}>
        <button
          className="rp-toggle"
          title={rightOpen ? '收起右侧栏' : '展开右侧栏'}
          aria-label="切换右侧栏"
          onClick={() => toggleRight()}
        >
          {rightOpen ? '❯' : '❮'}
        </button>
        <RightPanel
          activeSessionId={activeSessionId}
          activeWorkspaceId={activeSession?.folderId ?? null}
          onClose={() => toggleRight(false)}
          onPickHistory={(sid) => onChangeSession(sid)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function MessageList({
  messages,
  sessionId,
  showSub,
  onDeleted,
}: {
  messages: UiMessage[]
  sessionId: string | null
  showSub: boolean
  onDeleted: (messageId: string) => void
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = scrollerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])
  const foldedSubs = showSub
    ? 0
    : messages.filter((m) => m.speaker?.kind === 'sub').length
  return (
    <div className="chat-scroll" ref={scrollerRef}>
      {messages.length === 0 && (
        <div className="chat-empty">还没有消息,从下方输入框开始对话。</div>
      )}
      {messages.map((m) => (
        <Bubble key={m.id} msg={m} sessionId={sessionId} onDeleted={onDeleted} />
      ))}
      {foldedSubs > 0 && (
        <div className="group-folded-note">
          👥 已折叠 {foldedSubs} 条子代理发言（点上方「子代理过程」展开）
        </div>
      )}
    </div>
  )
}

function Bubble({
  msg,
  sessionId,
  onDeleted,
}: {
  msg: UiMessage
  sessionId: string | null
  onDeleted: (messageId: string) => void
}) {
  const { text, images, files } = useMemo(() => splitAttachments(msg.text), [msg.text])
  const [copied, setCopied] = useState(false)
  const [deleting, setDeleting] = useState(false)
  /** 子代理的气泡：群聊里单独一种样式，且不参与「复制/删除」。 */
  const isSub = msg.speaker?.kind === 'sub'

  // 本轮工具自动产出的图片（后端随 toolEnd 推来）——正文里已经写过的剔除，
  // 避免模型自己写了 `![](路径)` 时重复展示一遍。
  const autoImages = useMemo(() => {
    const mentioned = new Set(images.map((img) => img.src))
    return (msg.generated ?? []).filter((src) => !mentioned.has(src))
  }, [msg.generated, images])

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(msg.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* 剪贴板不可用时静默（无痕模式等） */
    }
  }
  const deleteMsg = async () => {
    if (!sessionId || deleting) return
    setDeleting(true)
    try {
      await api.chatDeleteMessage(sessionId, msg.id)
      onDeleted(msg.id)
    } catch (e) {
      console.warn('delete message failed', e)
      setDeleting(false)
    }
  }
  // 兜底模型切换提示：一条浅色小条，不是模型说的话，也不该有复制/删除按钮。
  if (msg.fallbackNote) {
    return (
      <div className="bubble-fallback" title={msg.fallbackNote}>
        {msg.fallbackNote}
      </div>
    )
  }
  return (
    <div className={`bubble ${msg.role}${isSub ? ' is-sub' : ''}`}>
      <div className="bubble-head">
        <div className="bubble-role">
          {isSub ? (
            <>
              <span className="bubble-ava">{msg.speaker?.emoji || '🤖'}</span>
              <span className="bubble-speaker">{msg.speaker?.name}</span>
              <span className="bubble-tag">
                子代理{msg.subDone ? '' : ' · 进行中'}
              </span>
              {msg.speaker?.project && (
                <span
                  className="bubble-tag bubble-tag-proj"
                  title={`项目目录: ${msg.speaker.project}`}
                >
                  📁{' '}
                  {msg.speaker.project.split(/[\\/]/).filter(Boolean).pop()}
                </span>
              )}
            </>
          ) : msg.role === 'user' ? (
            '你'
          ) : (
            '助手'
          )}
        </div>
        {/* 子代理气泡是**本轮实时**的，落库的只有主代理回复 —— 所以不给
            复制/删除按钮（删了也只会从界面上消失）。 */}
        {!isSub && (
          <div className="bubble-actions">
            <button
              className="bubble-act"
              title="复制"
              onClick={() => void copyText()}
            >
              {copied ? '已复制' : '复制'}
            </button>
            <button
              className="bubble-act bubble-act-danger"
              title={sessionId ? '删除（下一轮对话不再带上它）' : '删除'}
              disabled={!sessionId || deleting}
              onClick={() => void deleteMsg()}
            >
              {deleting ? '删除中…' : '删除'}
            </button>
          </div>
        )}
      </div>
      {isSub && msg.subTask && (
        <div className="bubble-subtask" title={msg.subTask}>
          📋 {clampLine(msg.subTask)}
        </div>
      )}
      {text && (
        <div className="bubble-text">
          {text.split('\n').map((line, i) => (
            <p key={i}>{clampLine(line) || '\u00a0'}</p>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="bubble-attachments">
          {files.map((f, i) => (
            <span key={`${f.src}-${i}`} className="bubble-file-chip" title={f.src}>
              📎 {f.name}
            </span>
          ))}
        </div>
      )}
      {images.length > 0 && (
        <div className="bubble-attachments">
          {images.map((img, i) => (
            <figure key={`${img.src}-${i}`} className="bubble-attachment">
              {img.url ? (
                <img
                  src={img.url}
                  alt={img.alt}
                  loading="lazy"
                  title={`点击预览大图 · ${img.src}`}
                  role="button"
                  tabIndex={0}
                  onClick={() =>
                    img.url &&
                    openImagePreview({ url: img.url, alt: img.alt, src: img.src })
                  }
                  onKeyDown={(e) => {
                    if ((e.key === 'Enter' || e.key === ' ') && img.url) {
                      e.preventDefault()
                      openImagePreview({ url: img.url, alt: img.alt, src: img.src })
                    }
                  }}
                  onError={(e) => {
                    // 文件被清理 / 不在 uploads（例如老会话里的绝对路径）
                    ;(e.currentTarget as HTMLImageElement).style.display = 'none'
                  }}
                />
              ) : null}
              <figcaption title={img.src}>🖼️ {img.alt}</figcaption>
            </figure>
          ))}
        </div>
      )}
      {autoImages.length > 0 && (
        <div className="bubble-generated">
          <div className="bubble-generated-title">🖼️ 本轮生成</div>
          <div className="bubble-attachments">
            {autoImages.map((src) => {
              const url = imageUrlFor(src)
              const alt = src.split(/[\\/]/).pop() || '生成的图片'
              return (
                <figure key={src} className="bubble-attachment is-generated">
                  {url ? (
                    <img
                      src={url}
                      alt={alt}
                      loading="lazy"
                      title={`点击预览大图 · ${src}`}
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        if (url) openImagePreview({ url, alt, src })
                      }}
                      onKeyDown={(e) => {
                        if ((e.key === 'Enter' || e.key === ' ') && url) {
                          e.preventDefault()
                          openImagePreview({ url, alt, src })
                        }
                      }}
                      onError={(e) => {
                        // 文件被清理 / 不在可读根内
                        ;(e.currentTarget as HTMLImageElement).style.display = 'none'
                      }}
                    />
                  ) : null}
                  <figcaption title={src}>🖼️ {alt}</figcaption>
                </figure>
              )
            })}
          </div>
        </div>
      )}
      {msg.toolCalls && msg.toolCalls.length > 0 && (
        <ToolStack calls={msg.toolCalls} />
      )}
      {/* 子代理在自己的循环里调的工具（read_image / shell / 检索…）——
          让「它到底干了什么」可见，而不是只有一个最终答复。 */}
      {isSub && msg.subTools && msg.subTools.length > 0 && (
        <ToolStack calls={msg.subTools} />
      )}
    </div>
  )
}

/**
 * 工具卡片的展开状态 —— **全局持久化**的纯 UI 偏好。
 *
 * 默认「缩小显示」（只留一行：图标 + 工具名 + 状态），点开头行才展开调用参数
 * 与输出。这个开关只写 localStorage，**绝不随 chat 帧进后端、也不进模型上下文** ——
 * 工具结果该给的照给，这里改的只是「用户想不想看细节」。
 */
const TOOL_OPEN_KEY = 'openminis:tool-open'

function readToolOpen(): boolean {
  try {
    return localStorage.getItem(TOOL_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

function writeToolOpen(open: boolean): void {
  try {
    localStorage.setItem(TOOL_OPEN_KEY, open ? '1' : '0')
  } catch {
    /* 隐私模式下写不了，忽略 */
  }
}

/** 整段工具调用（同一回合里连着调的那批）的展开状态 —— 同样是纯 UI 偏好。 */
const TOOL_GROUP_OPEN_KEY = 'openminis:tool-group-open'

function readGroupOpen(): boolean {
  try {
    return localStorage.getItem(TOOL_GROUP_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

function writeGroupOpen(open: boolean): void {
  try {
    localStorage.setItem(TOOL_GROUP_OPEN_KEY, open ? '1' : '0')
  } catch {
    /* 隐私模式下写不了，忽略 */
  }
}

/**
 * 一段工具调用的容器 —— **整段收成一行**。
 *
 * 一次任务常常连着调十几二十次工具，逐张铺开就是几十行噪音，把回答本身挤没了。
 * 所以 ≥2 次调用时收成一行（`🔧 工具调用 ×20 · 全部完成 ✓ · 1.4s`），点开才逐条
 * 列出，每条再各自折叠/展开（看参数与输出）。
 *
 * **正在跑的时候默认展开**（过程要看得见），一轮跑完自动收起 —— 除非你手动点过，
 * 那就听你的（偏好全局记住）。折叠只写 localStorage：**不进上下文、不影响模型
 * 看到的工具结果**，纯粹是「你想不想看」。
 */
function ToolStack({ calls }: { calls: ToolCallCard[] }) {
  const running = calls.some((c) => c.ok === undefined)
  const failed = calls.filter((c) => c.ok === false).length
  // 跑着 → 展开看进度；已完成 → 收起（尊重上次的手动选择）。
  const [open, setOpen] = useState<boolean>(() => running || readGroupOpen())
  /** 手动点过就不自动收起 —— 别跟人抢开关。 */
  const touched = useRef(false)

  useEffect(() => {
    // 跑完回到「偏好」：默认是收起（整段变一行）；上次手动展开过的则保持展开。
    if (!touched.current && !running) setOpen(readGroupOpen())
  }, [running])

  // 只有一次调用就别套一层壳了 —— 一张卡自成一组没意义。
  if (calls.length <= 1) {
    return (
      <div className="tool-stack">
        {calls.map((c) => (
          <ToolCard key={c.id} call={c} />
        ))}
      </div>
    )
  }

  const totalMs = calls.reduce((n, c) => n + (c.ms ?? 0), 0)
  const last = calls[calls.length - 1]
  // 整段都是同一个工具 → 用它的图标；混着调就统一用扳手。
  const icon = calls.every((c) => c.name === calls[0].name) ? iconFor(calls[0].name) : '🔧'

  return (
    <div className={`tool-group${open ? ' is-open' : ' is-collapsed'}`}>
      <button
        className="tool-group-head"
        type="button"
        aria-expanded={open}
        onClick={() => {
          touched.current = true
          setOpen((v) => {
            const next = !v
            writeGroupOpen(next)
            return next
          })
        }}
      >
        <span className="tool-glyph">{icon}</span>
        <span className="tool-group-title">
          工具调用 <span className="tool-group-count">×{calls.length}</span>
        </span>
        <span className="tool-group-state">
          {running ? (
            <>
              <span className="tool-group-run">执行中…</span>
              {/* 窄屏用 CSS 把这半截藏掉，只留「执行中…」 */}
              <span className="tool-group-cur">{last?.name ?? ''}</span>
            </>
          ) : failed > 0 ? (
            `✓ ${calls.length - failed} ✗ ${failed}`
          ) : (
            '全部完成 ✓'
          )}
        </span>
        {totalMs > 0 && <span className="tool-ms">{formatMs(totalMs)}</span>}
        <span className="tool-caret" aria-hidden="true">
          {open ? '⌃' : '⌄'}
        </span>
      </button>
      {open && (
        <div className="tool-group-body">
          {calls.map((c) => (
            <ToolCard key={c.id} call={c} />
          ))}
        </div>
      )}
    </div>
  )
}

function ToolCard({ call }: { call: ToolCallCard }) {
  const [open, setOpen] = useState<boolean>(() => readToolOpen())
  const state =
    call.ok === undefined ? 'running' : call.ok ? 'ok' : 'err'
  const toggle = () =>
    setOpen((v) => {
      const next = !v
      writeToolOpen(next)
      return next
    })
  return (
    <div className={`tool-card tool-${state}${open ? ' is-open' : ' is-collapsed'}`}>
      <button className="tool-head" onClick={toggle} type="button">
        <span className="tool-glyph">{iconFor(call.name)}</span>
        <span className="tool-name">{call.name}</span>
        <span className="tool-state">
          {state === 'running' ? '执行中…' : state === 'ok' ? '✓' : '✗'}
        </span>
        {call.ms !== undefined && (
          <span className="tool-ms">{formatMs(call.ms)}</span>
        )}
        <span className="tool-caret" aria-hidden="true">
          {open ? '⌃' : '⌄'}
        </span>
      </button>
      {open && (
        <div className="tool-body">
          <div className="tool-section">
            <div className="tool-section-title">调用</div>
            <code>{JSON.stringify(call.input, null, 2)}</code>
          </div>
          {call.output !== undefined && (
            <div className="tool-section">
              <div className="tool-section-title">结果</div>
              <pre>{call.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function iconFor(name: string): string {
  if (name.includes('shell') || name.includes('bash')) return '⚡'
  if (name.includes('read') || name.includes('file')) return '📄'
  if (name.includes('search')) return '🔍'
  if (name.includes('browser')) return '🌐'
  return '🔧'
}

/** 工具耗时显示：不足 1 秒给毫秒，1 分钟内给一位小数的秒，再长给分秒。 */
function formatMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m${s}s`
}

function Composer({
  disabled,
  busy = false,
  queued = 0,
  onSend,
  onStop,
  onClearSession,
}: {
  disabled: boolean
  /** 正在生成 → 输入框仍可用（消息排队），发送键变「暂停」。 */
  busy?: boolean
  /** 已排队待发的消息条数。 */
  queued?: number
  onSend: (text: string) => void
  onStop?: () => void
  onClearSession?: () => void
}) {
  const [draft, setDraft] = useState('')
  const [recording, setRecording] = useState(false)
  const [attachBusy, setAttachBusy] = useState(false)
  const [attachError, setAttachError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const imageInputRef = useRef<HTMLInputElement | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  /**
   * 草稿里的图片引用 —— 渲染成输入框上方的缩略图，让「我要发哪张图」看得见
   * （以前只插一行 `![name](path)` 文本，完全靠脑补）。点图放大，× 移除该引用。
   */
  const draftImages = useMemo(() => {
    const out: {
      alt: string
      src: string
      url: string
      raw: string
      index: number
    }[] = []
    const re = new RegExp(IMAGE_REF_RE.source, 'g')
    let m: RegExpExecArray | null
    while ((m = re.exec(draft))) {
      const src = m[2]
      const url = imageUrlFor(src)
      if (url) {
        out.push({
          alt: m[1] || '图片',
          src,
          url,
          raw: m[0],
          index: m.index,
        })
      }
    }
    return out
  }, [draft])

  /** 从草稿里删掉某条图片引用（按位置切片，避免误删同名图）。 */
  const removeDraftImage = (index: number, raw: string) => {
    setDraft((d) => {
      const cut =
        d.slice(index, index + raw.length) === raw
          ? d.slice(0, index) + d.slice(index + raw.length)
          : d.replace(raw, '')
      return cut.replace(/\n{3,}/g, '\n\n')
    })
  }

  const insertTool = (prefix: string) => {    const ta = document.querySelector<HTMLTextAreaElement>('form.composer textarea')
    if (!ta || disabled) return
    const pos = ta.selectionStart ?? ta.value.length
    const before = ta.value.slice(0, pos)
    const after = ta.value.slice(pos)
    const insert = before + prefix + after
    setDraft(insert)
    requestAnimationFrame(() => {
      ta.selectionStart = ta.selectionEnd = pos + prefix.length
      ta.focus()
    })
  }

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAttachBusy(true)
    setAttachError(null)
    try {
      // 先落盘拿到路径再插引用 —— 消息里只留路径，字节留在工作区。
      // 与 Kotlin 原版一致：非图片附件只进 <user-attached-files> 清单，
      // 内容不内联（文本附件的预览是给「人」看的，不影响模型侧开销）。
      const up = await api.uploadFile(file)
      const isText =
        file.type.startsWith('text/') ||
        /\.(json|md|py|js|ts|tsx|css|html|txt|csv|xml|yaml|yml|log|ini|cfg)$/i.test(file.name)
      if (isText) {
        const text = await file.text()
        insertTool(
          `[附件: ${file.name}](${up.path})\n${text.slice(0, 800)}${text.length > 800 ? '\n...(已截断)' : ''}`,
        )
      } else {
        insertTool(`[附件: ${file.name}](${up.path})`)
      }
    } catch (err) {
      setAttachError((err as Error).message)
    } finally {
      setAttachBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAttachBusy(true)
    setAttachError(null)
    try {
      // 旧实现是 FileReader.readAsDataURL → 把几 MB 的 base64 塞进输入框，
      // 受控 <textarea> 排版直接把页面拖死（「页面无响应」）。现在只传路径。
      const up = await api.uploadFile(file)
      insertTool(`![${file.name}](${up.path})`)
    } catch (err) {
      setAttachError((err as Error).message)
    } finally {
      setAttachBusy(false)
      if (imageInputRef.current) imageInputRef.current.value = ''
    }
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      mediaRecorderRef.current = mr
      audioChunksRef.current = []
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data)
      }
      mr.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        const url = URL.createObjectURL(blob)
        insertTool(`🎤 [语音消息](${url})`)
      }
      mr.start()
      setRecording(true)
    } catch {
      insertTool('⚠️ 无法访问麦克风')
    }
  }

  const stopRecording = () => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') {
      mr.stop()
    }
    mediaRecorderRef.current = null
    setRecording(false)
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault()
        if (!draft.trim() || disabled) return
        onSend(draft)
        setDraft('')
        if (fileInputRef.current) fileInputRef.current.value = ''
        if (imageInputRef.current) imageInputRef.current.value = ''
      }}
    >
      {/* Left tools — 保留文件/图片上传和清空会话 */}
      <div className="composer-left">
        <input ref={fileInputRef} type="file" accept="*/*" className="hidden-input" onChange={handleFileSelect} />
        <button
          type="button"
          className="composer-tool-btn"
          disabled={disabled || attachBusy}
          onClick={() => fileInputRef.current?.click()}
          title="上传文件（只把路径发给模型）"
        >
          📎
        </button>
        <input ref={imageInputRef} type="file" accept="image/*" className="hidden-input" onChange={handleImageUpload} />
        <button
          type="button"
          className="composer-tool-btn"
          disabled={disabled || attachBusy}
          onClick={() => imageInputRef.current?.click()}
          title="上传图片（默认只把路径发给模型，省上下文）"
        >
          {attachBusy ? '⏳' : '🖼️'}
        </button>
        <button
          type="button"
          className="composer-tool-btn"
          disabled={disabled}
          onClick={onClearSession}
          title="清空会话"
        >
          🗑️
        </button>
      </div>

      {/* Text input */}
      <div className="composer-input-wrap">
        {draftImages.length > 0 && (
          <div className="composer-attach-strip">
            {draftImages.map((im) => (
              <div
                key={`${im.index}-${im.src}`}
                className="composer-attach-thumb"
              >
                <img
                  src={im.url}
                  alt={im.alt}
                  title={`点击预览大图 · ${im.src}`}
                  onClick={() =>
                    openImagePreview({ url: im.url, alt: im.alt, src: im.src })
                  }
                />
                <button
                  type="button"
                  className="thumb-x"
                  title="移除这张图"
                  onClick={() => removeDraftImage(im.index, im.raw)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          rows={1}
          placeholder={
            disabled
              ? '先选择一个会话…'
              : attachBusy
                ? '上传中…'
                : busy
                  ? '继续输入，回车加入队列…'
                  : '输入消息…'
          }
          value={draft}
          disabled={disabled}
          onChange={(e) => {
            // 双重保险：粘贴超大内容时先剥掉内联 base64，再限长 ——
            // 一个几百万字符的单行足以让整个页面无响应。
            let v = stripInlineData(e.target.value)
            if (v.length > MAX_DRAFT_CHARS) {
              v = `${v.slice(0, MAX_DRAFT_CHARS)}\n…（内容过长已截断，请改用 📎 上传文件）`
            }
            setDraft(v)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              // NOTE: keep the semicolon — without it ASI glues this line onto
              // ``e.preventDefault()`` and Enter throws "not a function".
              e.preventDefault()
              const form = (e.currentTarget as HTMLTextAreaElement).form
              form?.requestSubmit()
            }
          }}
        />
        {attachError && (
          <div className="composer-note err">附件上传失败：{attachError}</div>
        )}
        {queued > 0 && (
          <div className="composer-note">
            📥 已排队 {queued} 条，本轮结束后自动发送
          </div>
        )}
      </div>

      {/* Right actions */}
      <div className="composer-right">
        <button
          type="button"
          className={`composer-rec-btn ${recording ? 'recording' : ''}`}
          disabled={disabled}
          onClick={recording ? stopRecording : startRecording}
          title={recording ? '停止录音' : '语音输入'}
        >
          🎤
        </button>
        {busy && (
          <button
            type="button"
            className="composer-stop-btn"
            onClick={() => onStop?.()}
            title="暂停当前生成（已排队/已输入的内容不受影响）"
          >
            ⏹
          </button>
        )}
        <button
          type="submit"
          className="composer-send-btn"
          disabled={disabled || !draft.trim()}
          title={busy ? '加入队列（本轮结束后自动发送）' : '发送'}
        >
          ↑
        </button>
      </div>
    </form>
  )
}
