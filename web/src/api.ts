/** Thin client for the FastAPI backend. No business logic lives here. */

import type {
  AppInfo,
  ChatMessageInfo,
  ChatSessionInfo,
  ConfigFieldInfo,
  ConfigUpdateResult,
  FetchModelsRequest,
  FetchModelsResponse,
  FsNode,
  FsReadResult,
  FsRoot,
  HealthInfo,
  HistoryEntry,
  KnowledgeContent,
  KnowledgeList,
  MemoryDoc,
  MemoryList,
  ServerFrame,
  SettingsInfo,
  SettingsPayload,
  SkillDetail,
  SkillInfo,
  SkillsList,
  StorageInfo,
  SystemLogs,
  WorkspaceInfo,
} from './types'

const API = '/api'
const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<HealthInfo>('/health'),

  configTopics: () => request<string[]>('/config/topics'),

  configList: (topic?: string) =>
    request<ConfigFieldInfo[]>(`/config${topic ? `/?topic=${encodeURIComponent(topic)}` : ''}`),

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

  // -- skills (技能) ----------------------------------------------
  skillsList: () => request<SkillsList>('/skills'),

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

  // -- knowledge (知识库搜索) -------------------------------------
  knowledgeSearch: (q = '', kind = '', limit = 80) =>
    request<KnowledgeList>(
      `/knowledge?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}&limit=${limit}`,
    ),

  knowledgeContent: (kind: string, name: string) =>
    request<KnowledgeContent>(`/knowledge/content/${kind}/${encodeURIComponent(name)}`),
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

/** Standalone helper: creates a WebSocket with auto-reconnect.
 * Kept separate from openSocket so the function is hoisted above its call site,
 * avoiding a TDZ error under module: ESNext + isolatedModules. */
function makeSocket(
  onFrame: (frame: ServerFrame) => void,
  opts: OpenSocketOptions,
  getState: () => SocketState,
  setState: (s: SocketState) => void,
  pendingRef: { current: string[] },
  backoffRef: { current: number },
  closedRef: { current: boolean },
  closingRef: { current: boolean },
): WebSocket {
  setState('connecting')
  const sock = new WebSocket(WS_URL)
  sock.onmessage = (event) => {
    try {
      onFrame(JSON.parse(event.data as string) as ServerFrame)
    } catch {
      onFrame({ type: 'error', error: 'bad frame from server' })
    }
  }
  sock.onopen = () => {
    backoffRef.current = 1000
    for (const m of pendingRef.current) sock.send(m)
    pendingRef.current = []
    setState('open')
  }
  sock.onclose = () => {
    if (closedRef.current || closingRef.current) {
      setState('closed')
      return
    }
    setState('connecting')
    const wait = backoffRef.current
    backoffRef.current = Math.min(backoffRef.current * 2, 30_000)
    setTimeout(() => {
      if (closedRef.current || closingRef.current) return
      try {
        makeSocket(onFrame, opts, getState, setState, pendingRef, backoffRef, closedRef, closingRef)
      } catch {
        /* ignore */
      }
    }, wait)
  }
  sock.onerror = () => {
    try {
      sock.close()
    } catch {
      /* ignore */
    }
  }
  return sock
}

export function openSocket(
  onFrame: (frame: ServerFrame) => void,
  opts: OpenSocketOptions = {},
): OpenSocketHandle {
  let state: SocketState = 'connecting'
  let closed = false
  let closing = false
  let backoff = 1000
  const pending: string[] = []

  const emit = () => opts.onStateChange?.(state)

  function setState(s: SocketState) {
    if (state === s) return
    state = s
    emit()
  }

  // makeSocket is defined above; calling it here is safe.
  let ws: WebSocket = makeSocket(onFrame, opts, () => state, setState, { current: pending }, { current: backoff }, { current: closed }, { current: closing })

  const handle: OpenSocketHandle = {
    send: (data: string) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data)
      } else {
        pending.push(data)
      }
    },
    close: () => {
      closed = true
      closing = true
      setState('closing')
      try {
        ws.close()
      } catch {
        /* ignore */
      }
    },
    get readyState() {
      return ws.readyState
    },
    get state() {
      return state
    },
  }
  return handle
}
