/** Thin client for the FastAPI backend. No business logic lives here. */

import type {
  AppInfo,
  ChatMessageInfo,
  ChatSessionInfo,
  ConfigFieldInfo,
  ConfigUpdateResult,
  ConsoleStatus,
  FetchModelsRequest,
  FetchModelsResponse,
  FsNode,
  FsReadResult,
  FsRoot,
  GuardEvent,
  GuardEventList,
  GuardLiveItem,
  GuardScope,
  HealthInfo,
  HistoryEntry,
  KnowledgeContent,
  KnowledgeGraph,
  KnowledgeList,
  MarketplaceInstallResult,
  MarketplaceSource,
  MemoryDoc,
  MemoryList,
  OrganizeResponse,
  PluginLogs,
  PluginStatus,
  PluginsList,
  ScheduledRunInfo,
  ScheduledTaskDraft,
  ScheduledTaskInfo,
  ServerFrame,
  SettingsInfo,
  SettingsPayload,
  SkillDetail,
  SkillEnvInfo,
  SkillInfo,
  SkillsList,
  StorageInfo,
  SubagentInfo,
  SubagentPlanResult,
  SubagentRegistry,
  SubagentsList,
  SystemLogs,
  UsageStats,
  WorkspaceInfo,
} from './types'

const API = '/api'
const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...init,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    // 423 = 页面锁着（后端 `_access_gate` 只放行解锁接口本身）。
    // 广播出去让 App 换成锁屏 —— 令牌过期时不必等用户手动刷新。
    if (res.status === 423) {
      window.dispatchEvent(new CustomEvent('openminis:locked'))
    }
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<HealthInfo>('/health'),

  configTopics: () => request<string[]>('/config/topics'),

  // No trailing slash: `/api/config/` is swallowed by the `/api/config/{path}`
  // route (path="") and 404s, which silently broke the 外观 topic editor.
  configList: (topic?: string) =>
    request<ConfigFieldInfo[]>(`/config${topic ? `?topic=${encodeURIComponent(topic)}` : ''}`),

  configGet: (path: string) => request<unknown>(`/config/${path}`),

  configSet: (path: string, value: unknown) =>
    request<ConfigUpdateResult>(`/config/${path}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),

  settingsGet: () => request<SettingsInfo>('/settings'),

  settingsPut: (payload: SettingsPayload) =>
    request<SettingsInfo>('/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  fetchModels: (payload: FetchModelsRequest) =>
    request<FetchModelsResponse>('/settings/fetch-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  chatSessions: (workspace?: string) =>
    request<{ sessions: ChatSessionInfo[] }>(
      `/chats/sessions${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''}`,
    ),

  chatCreate: (folderId?: string | null) =>
    request<ChatSessionInfo>('/chats/sessions', {
      method: 'POST',
      body: JSON.stringify(folderId === undefined ? {} : { folderId }),
    }),

  chatDelete: (id: string) =>
    request<{ ok: boolean }>(`/chats/sessions/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  chatMessages: (id: string) =>
    request<{ messages: ChatMessageInfo[] }>(
      `/chats/sessions/${encodeURIComponent(id)}/messages`,
    ),

  chatDeleteMessage: (sessionId: string, messageId: string) =>
    request<{ ok: boolean }>(
      `/chats/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
      { method: 'DELETE' },
    ),

  chatMove: (id: string, folderId: string | null) =>
    request<{ ok: boolean }>(
      `/chats/sessions/${encodeURIComponent(id)}/workspace`,
      {
        method: 'PATCH',
        body: JSON.stringify({ folderId }),
      },
    ),

  workspaces: () =>
    request<{ workspaces: WorkspaceInfo[] }>('/chats/workspaces'),

  workspaceCreate: (name: string, description = '', path = '') =>
    request<WorkspaceInfo>('/chats/workspaces', {
      method: 'POST',
      body: JSON.stringify({ name, description, path }),
    }),

  workspaceRename: (id: string, name: string, description?: string, path?: string) =>
    request<WorkspaceInfo>(`/chats/workspaces/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ name, description, path }),
    }),

  workspaceDelete: (id: string) =>
    request<{ ok: boolean }>(`/chats/workspaces/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  fsRoot: (workspace?: string) =>
    request<FsRoot>(
      `/fs/root${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''}`,
    ),
  fsTree: (path = '', depth = 2, workspace?: string) =>
    request<FsNode>(
      `/fs/tree?path=${encodeURIComponent(path)}&depth=${depth}${
        workspace ? `&workspace=${encodeURIComponent(workspace)}` : ''
      }`,
    ),
  fsRead: (path: string, maxBytes = 80_000, workspace?: string) =>
    request<FsReadResult>(
      `/fs/read?path=${encodeURIComponent(path)}&maxBytes=${maxBytes}${
        workspace ? `&workspace=${encodeURIComponent(workspace)}` : ''
      }`,
    ),

  history: async (): Promise<HistoryEntry[]> => {
    const { sessions } = await request<{ sessions: ChatSessionInfo[] }>(
      '/chats/sessions',
    )
    const out: HistoryEntry[] = []
    for (const s of sessions.slice(0, 50)) {
      const r = await request<{ messages: ChatMessageInfo[] }>(
        `/chats/sessions/${encodeURIComponent(s.id)}/messages`,
      )
      for (const m of r.messages) {
        if (m.role === 'user') {
          out.push({
            sessionId: s.id,
            sessionTitle: s.title,
            messageId: m.id,
            text: m.text,
            createdAt: m.createdAt,
          })
        }
      }
    }
    return out.sort((a, b) => b.createdAt - a.createdAt)
  },

  // -- settings system helpers (设置 → 记忆/日志/存储/备份/关于) --------
  appinfo: () => request<AppInfo>('/system/appinfo'),

  systemLogs: (lines = 600) => request<SystemLogs>(`/system/logs?lines=${lines}`),

  systemStorage: () => request<StorageInfo>('/system/storage'),

  memoryList: () => request<MemoryList>('/system/memory'),

  memoryGet: (name: string) =>
    request<MemoryDoc>(`/system/memory/${encodeURIComponent(name)}`),

  memoryPut: (name: string, content: string) =>
    request<MemoryDoc>(`/system/memory/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

  memoryDelete: (name: string) =>
    request<{ ok: boolean; name: string }>(
      `/system/memory/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ),

  memoryOrganize: () =>
    request<OrganizeResponse>('/system/memory/organize', { method: 'POST' }),

  // -- skills (技能) ----------------------------------------------
  skillsList: () => request<SkillsList>('/skills'),

  /** 技能声明的环境变量 + 当前值（与设置页的「环境变量」是同一份存储）。 */
  skillsEnv: () => request<SkillEnvInfo>('/skills/env'),

  /** 整份覆盖环境变量。空值 = 删掉该项；存完沙箱命令立刻能用。 */
  skillsEnvSave: (values: Record<string, string>) =>
    request<{ ok: boolean } & SkillEnvInfo>('/skills/env', {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }),

  skillGet: (name: string) =>
    request<SkillDetail>(`/skills/${encodeURIComponent(name)}`),

  skillInstall: (source: string, force = false) =>
    request<{ ok: boolean; skill: SkillInfo }>('/skills/install', {
      method: 'POST',
      body: JSON.stringify({ source, force }),
    }),

  skillDelete: (name: string) =>
    request<{ ok: boolean; name: string }>(
      `/skills/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ),

  /** Put a skill in the main agent's scope (it becomes callable). */
  skillActivate: (name: string) =>
    request<{ ok: boolean; name: string; active: string[] }>(
      `/skills/${encodeURIComponent(name)}/activate`,
      { method: 'POST' },
    ),

  skillDeactivate: (name: string) =>
    request<{ ok: boolean; name: string; active: string[] }>(
      `/skills/${encodeURIComponent(name)}/deactivate`,
      { method: 'POST' },
    ),

  // -- plugins (插件：通道 / 桥接 / 外部程序，含导入的第三方包) --------
  pluginsList: () => request<PluginsList>('/plugins'),

  pluginDetail: (id: string) =>
    request<PluginStatus>(`/plugins/${encodeURIComponent(id)}`),

  /**
   * 安装随引擎发布的内置插件（如 qq-bot）到数据目录，之后可改配置。
   * `refresh` 会用包内那份覆盖已装的清单（保留用户配置）—— 内置插件装过之后
   * 不会自动跟着升级，新加的配置项否则永远不出现。
   */
  pluginInstall: (id: string, refresh = false) =>
    request<{ ok: boolean; id: string; plugin: PluginStatus }>('/plugins/install', {
      method: 'POST',
      body: JSON.stringify({ id, refresh }),
    }),

  /** 导入本机路径的插件包（zip 或目录），没有 plugin.json 也能装。 */
  pluginImport: (path: string) =>
    request<{ ok: boolean; id: string; plugin: PluginStatus }>('/plugins/import', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),

  /** 上传 zip 插件包并导入（multipart，不能走 request()）。 */
  pluginUpload: async (
    file: File,
  ): Promise<{ ok: boolean; id: string; plugin: PluginStatus }> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${API}/plugins/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

  pluginRemove: (id: string) =>
    request<{ ok: boolean }>(`/plugins/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  /** 保存配置：密钥字段留空 = 不改（界面不回传明文密钥）。 */
  pluginConfigSave: (id: string, values: Record<string, unknown>) =>
    request<{ ok: boolean; plugin: PluginStatus }>(
      `/plugins/${encodeURIComponent(id)}/config`,
      { method: 'PUT', body: JSON.stringify({ values }) },
    ),

  pluginAction: (id: string, action: 'start' | 'stop' | 'restart') =>
    request<{ ok: boolean; plugin: PluginStatus }>(
      `/plugins/${encodeURIComponent(id)}/${action}`,
      { method: 'POST' },
    ),

  pluginLogs: (id: string, limit = 120) =>
    request<PluginLogs>(`/plugins/${encodeURIComponent(id)}/logs?limit=${limit}`),

  // -- marketplace (技能广场) --------------------------------------
  marketplaceSources: () =>
    request<{ sources: MarketplaceSource[] }>('/marketplace'),

  marketplaceInstallUrl: (url: string, force: boolean) =>
    request<MarketplaceInstallResult>('/marketplace/install-url', {
      method: 'POST',
      body: JSON.stringify({ url, force }),
    }),

  // -- knowledge (知识库搜索) -------------------------------------
  knowledgeSearch: (q = '', kind = '', limit = 80, category = '') =>
    request<KnowledgeList>(
      `/knowledge?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}` +
        `&category=${encodeURIComponent(category)}&limit=${limit}`,
    ),

  knowledgeContent: (kind: string, name: string) =>
    request<KnowledgeContent>(`/knowledge/content/${kind}/${encodeURIComponent(name)}`),

  knowledgeGraph: () => request<KnowledgeGraph>('/knowledge/graph'),

  // -- 沙箱守卫（拦截事件 + 放行 + 控制台密码）----------------------
  guardLive: () => request<{ items: GuardLiveItem[] }>('/guard/live?limit=30'),

  guardEvents: (family = '') =>
    request<GuardEventList>(
      `/guard/events?limit=200&family=${encodeURIComponent(family)}`,
    ),

  guardAllow: (id: string, scope: GuardScope) =>
    request<{ ok: boolean; event: GuardEvent }>(
      `/guard/events/${encodeURIComponent(id)}/allow`,
      { method: 'POST', body: JSON.stringify({ scope }) },
    ),

  guardDeny: (id: string) =>
    request<{ ok: boolean; event: GuardEvent }>(
      `/guard/events/${encodeURIComponent(id)}/deny`,
      { method: 'POST' },
    ),

  guardClear: () => request<{ ok: boolean }>('/guard/events/clear', { method: 'POST' }),

  guardAllowlist: () =>
    request<{ allowlist: Record<string, string[]> }>('/guard/allowlist'),

  guardRevoke: (family = '', key = '') =>
    request<{ ok: boolean; allowlist: Record<string, string[]> }>(
      `/guard/allowlist/revoke?family=${encodeURIComponent(family)}` +
        `&key=${encodeURIComponent(key)}`,
      { method: 'POST' },
    ),

  consoleStatus: () => request<ConsoleStatus>('/guard/console/status'),

  consoleUnlock: (password: string) =>
    request<{ ok: boolean; ttlSeconds: number }>('/guard/console/unlock', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  consoleLock: () => request<{ ok: boolean }>('/guard/console/lock', { method: 'POST' }),

  consoleSetPassword: (password: string, current = '') =>
    request<ConsoleStatus>('/guard/console/password', {
      method: 'POST',
      body: JSON.stringify({ password, current }),
    }),

  accessStatus: () => request<ConsoleStatus>('/guard/access/status'),

  accessUnlock: (password: string) =>
    request<{ ok: boolean; ttlSeconds: number }>('/guard/access/unlock', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  accessLock: () => request<{ ok: boolean }>('/guard/access/lock', { method: 'POST' }),

  accessSetPassword: (password: string, current = '') =>
    request<ConsoleStatus>('/guard/access/password', {
      method: 'POST',
      body: JSON.stringify({ password, current }),
    }),

  // -- subagents (助理 / 子代理) ---------------------------------
  scheduledList: () =>
    request<{ tasks: ScheduledTaskInfo[]; now: number }>('/scheduled/tasks'),

  scheduledCreate: (payload: ScheduledTaskDraft) =>
    request<{ task: ScheduledTaskInfo }>('/scheduled/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  scheduledUpdate: (id: string, payload: Partial<ScheduledTaskDraft>) =>
    request<{ task: ScheduledTaskInfo }>(`/scheduled/tasks/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  scheduledDelete: (id: string) =>
    request<{ ok: boolean; id: string }>(`/scheduled/tasks/${id}`, {
      method: 'DELETE',
    }),

  scheduledToggle: (id: string, enabled: boolean) =>
    request<{ task: ScheduledTaskInfo }>(`/scheduled/tasks/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  scheduledRuns: (id: string) =>
    request<{ runs: ScheduledRunInfo[] }>(`/scheduled/tasks/${id}/runs`),

  usageStats: () => request<UsageStats>('/usage'),

  /**
   * 上传背景图。不能走 ``request()`` —— 它固定写 ``Content-Type: application/json``，
   * 而 multipart 必须让浏览器自己带 boundary。
   */
  appearanceUploadBackground: async (file: File): Promise<{ spec: string; url: string }> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${API}/appearance/background`, { method: 'POST', body: form })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json() as Promise<{ spec: string; url: string }>
  },

  /**
   * 上传聊天附件（图片/文件）。同样不能用 ``request()`` —— multipart 需要浏览器
   * 自己带 boundary。返回里最关键的是 ``path``：消息里只存这个路径，字节留在
   * 工作区（path-only，见 agent.imageContextMode）。
   */
  uploadFile: async (
    file: File,
  ): Promise<{
    name: string
    storedName: string
    path: string
    url: string
    mime: string
    size: number
    kind: 'image' | 'file'
  }> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${API}/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

  subagentsList: () => request<SubagentsList>('/subagents'),
  subagentsRegistry: () => request<SubagentRegistry>('/subagents/registry'),

  subagentsCreate: (payload: Partial<SubagentInfo>) =>
    request<{ subagent: SubagentInfo }>('/subagents', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  subagentsUpdate: (id: string, payload: Partial<SubagentInfo>) =>
    request<{ subagent: SubagentInfo }>(
      `/subagents/${encodeURIComponent(id)}`,
      { method: 'PUT', body: JSON.stringify(payload) },
    ),

  subagentsDelete: (id: string) =>
    request<{ ok: boolean }>(`/subagents/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  /** Hand the registry to the main agent's LLM; returns a candidate config. */
  subagentsPlan: (req: string, autoSave = false) =>
    request<SubagentPlanResult>('/subagents/plan', {
      method: 'POST',
      body: JSON.stringify({ request: req, autoSave }),
    }),
}

/** Trigger a browser download for a backend file endpoint (GET). */
export function downloadUrl(path: string) {
  const a = document.createElement('a')
  a.href = `${API}${path}`
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/**
 * Open the shared WebSocket. The backend streams `delta` frames and closes a
 * turn with `done`; see openminis/server/main.py.
 *
 * 自动重连:断开后 1s/2s/4s 退避重连,直到 30s 上限。``send`` / ``close`` 在
 * 重连期间保持可用:未连上时 ``send`` 会把帧排队,新 socket 一开就 flush。
 * ``readyState`` 与 ``onStateChange`` 让 UI 能避开 "假装连上了" 的坑
 * (proxy 之前没暴露 readyState,ChatView 永远读到 undefined)。
 */
export type SocketState = 'connecting' | 'open' | 'closing' | 'closed'

export interface OpenSocketOptions {
  /** Called on every WebSocket state transition. Cheap; safe to call React
   *  setters from here — they're invoked synchronously from the browser event
   *  loop. */
  onStateChange?: (s: SocketState) => void
  /** 断线后**重新**连上的那一刻回调一次（首次连接不算）。
   *  断线期间的流式帧是发给旧连接的，收不回来了 —— 调用方据此重拉历史、
   *  清掉「在跑」标记，否则界面会一直卡在生成中，只能刷新页面。 */
  onReconnect?: () => void
}

export interface OpenSocketHandle {
  send: (data: string) => void
  close: () => void
  /** Reflects the current underlying WebSocket's readyState. Always defined,
   *  so callers can do ``handle.readyState !== WebSocket.OPEN`` safely. */
  readyState: number
  /** Sub-state for UI: ``'connecting'`` during exponential backoff,
   *  ``'open'`` while connected, ``'closing'`` after ``close()`` was called,
   *  ``'closed'`` otherwise. */
  state: SocketState
}

/** Creates a WebSocket with auto-reconnect, heartbeat and wake-up resume.
 *
 *  2026-09 修复：以前重连出来的新 socket **没有写回** ``ws`` 变量 —— 老连接
 *  一断，``handle.send`` / ``readyState`` 就永远盯着那把已经死掉的 socket，
 *  于是重连虽然悄悄成功，消息却全塞进队列发不出去（只有下次重连的 onopen
 *  才会 flush）。用户侧的体感就是「断线后怎么点都没反应，只能刷新页面」。
 *  现在当前连接存在闭包里，每次 (重) 连都覆盖它，并顺手加上心跳探活。
 */
export function openSocket(
  onFrame: (frame: ServerFrame) => void,
  opts: OpenSocketOptions = {},
): OpenSocketHandle {
  let state: SocketState = 'connecting'
  let closed = false
  let closing = false
  let backoff = 1000
  const pending: string[] = []
  /** 当前这把连接。重连会换成新的，必须换掉这个引用（见上面的注释）。 */
  let ws: WebSocket | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let beatTimer: ReturnType<typeof setInterval> | null = null
  /** 最近一次收到服务端帧（含 pong）的时间 —— 用来判链路是不是已经死了。 */
  let lastSeen = 0
  /** 是否成功连上过：用来区分「首次连接」和「断线重连」。 */
  let everOpened = false

  const HEARTBEAT_MS = 20_000
  /** 这么久没有任何回包就当作链路已死（休眠/切网/NAT 超时都不会发 FIN）。 */
  const STALE_MS = 55_000

  const emit = () => opts.onStateChange?.(state)

  function setState(s: SocketState) {
    if (state === s) return
    state = s
    emit()
  }

  function stopBeat() {
    if (beatTimer !== null) {
      clearInterval(beatTimer)
      beatTimer = null
    }
  }

  function scheduleRetry() {
    if (closed || closing || retryTimer !== null) return
    setState('connecting')
    const wait = backoff
    backoff = Math.min(backoff * 2, 30_000)
    retryTimer = setTimeout(() => {
      retryTimer = null
      connect()
    }, wait)
  }

  /** 立刻（重）连 —— 退避重连、心跳判死、页面重新可见都走这里。 */
  function connect() {
    if (closed || closing) return
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    setState('connecting')
    let sock: WebSocket
    try {
      sock = new WebSocket(WS_URL)
    } catch {
      scheduleRetry()
      return
    }
    ws = sock
    sock.onmessage = (event) => {
      lastSeen = Date.now()
      let frame: ServerFrame
      try {
        frame = JSON.parse(event.data as string) as ServerFrame
      } catch {
        onFrame({ type: 'error', error: 'bad frame from server' })
        return
      }
      // 心跳回包只用来探活，不进业务帧流。
      if ((frame as { type?: string }).type === 'pong') return
      onFrame(frame)
    }
    sock.onopen = () => {
      backoff = 1000
      lastSeen = Date.now()
      const queued = pending.splice(0, pending.length)
      for (const m of queued) {
        try {
          sock.send(m)
        } catch {
          pending.push(m)
        }
      }
      setState('open')
      if (everOpened) opts.onReconnect?.()
      everOpened = true
      stopBeat()
      beatTimer = setInterval(() => {
        if (ws !== sock || sock.readyState !== WebSocket.OPEN) return
        if (Date.now() - lastSeen > STALE_MS) {
          // 发得出去、收不回来 = 链路已死：主动关掉交给 onclose 重连，
          // 比等 TCP 自己超时（可能几分钟）快得多。
          try {
            sock.close()
          } catch {
            /* ignore */
          }
          return
        }
        try {
          sock.send(JSON.stringify({ type: 'ping' }))
        } catch {
          /* ignore */
        }
      }, HEARTBEAT_MS)
    }
    sock.onclose = (event) => {
      stopBeat()
      // 4401 = 服务端因「页面已锁定」拒绝握手（WS 不走 /api/，闸门单独判定）。
      // 这种情况别重连 —— 广播出去让 App 换成锁屏。
      if (event.code === 4401) {
        closed = true
        setState('closed')
        window.dispatchEvent(new CustomEvent('openminis:locked'))
        return
      }
      if (closed || closing) {
        setState('closed')
        return
      }
      scheduleRetry()
    }
    sock.onerror = () => {
      try {
        sock.close()
      } catch {
        /* ignore */
      }
    }
  }

  /** 页面重新可见 / 网络恢复 / 从缓存恢复：不等退避，立刻试一次。 */
  function kick() {
    if (closed || closing) return
    if (ws && ws.readyState === WebSocket.OPEN) return
    connect()
  }

  const onWake = () => {
    if (document.visibilityState === 'visible') kick()
  }
  document.addEventListener('visibilitychange', onWake)
  window.addEventListener('online', kick)
  window.addEventListener('pageshow', kick)

  connect()

  const handle: OpenSocketHandle = {
    send: (data: string) => {
      const sock = ws
      if (sock && sock.readyState === WebSocket.OPEN) sock.send(data)
      // 断网期间先排队，重连的 onopen 会一次性 flush；给个上限免得撑爆内存。
      else if (pending.length < 200) pending.push(data)
    },
    close: () => {
      closed = true
      closing = true
      if (retryTimer !== null) {
        clearTimeout(retryTimer)
        retryTimer = null
      }
      stopBeat()
      document.removeEventListener('visibilitychange', onWake)
      window.removeEventListener('online', kick)
      window.removeEventListener('pageshow', kick)
      setState('closing')
      try {
        ws?.close()
      } catch {
        /* ignore */
      }
    },
    get readyState() {
      return ws ? ws.readyState : WebSocket.CLOSED
    },
    get state() {
      return state
    },
  }
  return handle
}
