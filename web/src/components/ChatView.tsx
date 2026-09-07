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
  ServerFrame,
  WorkspaceInfo,
} from '../types'
import { RightPanel } from './RightPanel'

interface ChatViewProps {
  activeSessionId: string | null
  onChangeSession: (id: string) => void
  onBackToChat?: () => void
}

interface UiMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  toolCalls?: ToolCallCard[]
}

interface ToolCallCard {
  id: string
  name: string
  input: Record<string, unknown>
  ok?: boolean
  output?: string
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
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [configWarning, setConfigWarning] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [rightOpen, setRightOpen] = useState(true)

  const socketRef = useRef<OpenSocketHandle | null>(null)
  const [socketState, setSocketState] = useState<SocketState>('connecting')
  const activeIdRef = useRef<string | null>(activeSessionId)
  const pickerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    activeIdRef.current = activeSessionId
  }, [activeSessionId])

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

  // -- load messages when active session changes ----------------------------
  useEffect(() => {
    activeIdRef.current = activeSessionId
    if (!activeSessionId) {
      setMessages([])
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const data = await api.chatMessages(activeSessionId)
        if (!cancelled && activeIdRef.current === activeSessionId) {
          setMessages(
            data.messages.map<UiMessage>((m: ChatMessageInfo) => ({
              id: m.id,
              role: m.role,
              text: m.text,
              toolCalls: [],
            })),
          )
        }
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeSessionId])

  // -- one shared websocket ------------------------------------------------
  const handleFrame = useCallback((frame: ServerFrame) => {
    const sid = activeIdRef.current

    switch (frame.type) {
      case 'chatSession':
        activeIdRef.current = frame.sessionId
        onChangeSession(frame.sessionId)
        break
      case 'delta': {
        if (!sid) break
        setMessages((prev) => {
          const list = [...prev]
          let tail = list[list.length - 1]
          if (!tail || tail.role !== 'assistant') {
            tail = {
              id: `tmp-${Date.now()}`,
              role: 'assistant',
              text: '',
              toolCalls: [],
            }
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
        setMessages((prev) => {
          const list = [...prev]
          let tail = list[list.length - 1]
          if (!tail || tail.role !== 'assistant') {
            tail = {
              id: `tmp-${Date.now()}`,
              role: 'assistant',
              text: '',
              toolCalls: [],
            }
            list.push(tail)
          }
          list[list.length - 1] = {
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
        setMessages((prev) => {
          const list = [...prev]
          const tail = list[list.length - 1]
          if (!tail) return list
          const cards = (tail.toolCalls ?? []).map((c) =>
            c.id === frame.id
              ? { ...c, ok: frame.ok, output: frame.output }
              : c,
          )
          list[list.length - 1] = { ...tail, toolCalls: cards }
          return list
        })
        break
      }
      case 'done': {
        setBusy(false)
        const completed = activeIdRef.current
        if (completed) {
          void api.chatMessages(completed).catch(() => undefined)
          void reloadSessions()
        }
        break
      }
      case 'error':
        setError(frame.error)
        setBusy(false)
        break
      default:
        break
    }
  }, [onChangeSession, reloadSessions])

  useEffect(() => {
    const ws = openSocket(handleFrame, { onStateChange: setSocketState })
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
  const send = useCallback((text: string) => {
    if (!text.trim() || busy) return
    setError(null)
    setConfigWarning(null)
    const sid = activeIdRef.current
    if (!sid) return
    setBusy(true)
    setMessages((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: 'user', text, toolCalls: [] },
    ])
    const ws = socketRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // keep the user message + busy false — the proxy will queue the
      // frame and flush on the next open; a tiny pill surfaces the state.
      setError('连接已断开,正在重连…')
      setBusy(false)
      return
    }
    ws.send(JSON.stringify({ type: 'chat', text, sessionId: sid }))
  }, [busy])

  const createChatHere = useCallback(async (folderId?: string | null) => {
    try {
      const created = await api.chatCreate(folderId ?? undefined)
      await reloadSessions()
      onChangeSession(created.id)
      setMessages([])
      setError(null)
      setPickerOpen(false)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [onChangeSession, reloadSessions])

  // -- clear current session messages ---------------------------------------
  const clearSession = useCallback(() => {
    if (!activeSessionId) return
    setMessages([])
    setError(null)
  }, [activeSessionId])

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

  // -- render --------------------------------------------------------------
  return (
    <div className="view chat chat-with-rail">
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

          <button
            className="rp-toggle-edge"
            title={rightOpen ? '收起右侧栏' : '展开右侧栏'}
            onClick={() => setRightOpen((v) => !v)}
          >
            {rightOpen ? '收起工具栏 ❯' : '❮ 展开工具栏'}
          </button>
        </div>

        {configWarning && (
          <div className="config-banner">
            <span>{configWarning}</span>
            <button onClick={() => (window.location.hash = '#settings')}>
              打开设置
            </button>
          </div>
        )}
        <MessageList messages={messages} />
        {socketState !== 'open' && (
          <div className={`chat-sockpill chat-sockpill--${socketState}`}>
            {socketState === 'connecting' && '连接已断开,正在重连…'}
            {socketState === 'closing' && '正在关闭连接…'}
            {socketState === 'closed' && '连接已关闭'}
          </div>
        )}
        {error && <div className="chat-error">{error}</div>}
        <Composer disabled={busy || !activeSessionId} onSend={send} onClearSession={clearSession} />
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
          onClick={() => setRightOpen((v) => !v)}
        >
          {rightOpen ? '❯' : '❮'}
        </button>
        <RightPanel
          activeSessionId={activeSessionId}
          activeWorkspaceId={activeSession?.folderId ?? null}
          onClose={() => setRightOpen(false)}
          onPickHistory={(sid) => onChangeSession(sid)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function MessageList({ messages }: { messages: UiMessage[] }) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = scrollerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])
  return (
    <div className="chat-scroll" ref={scrollerRef}>
      {messages.length === 0 && (
        <div className="chat-empty">还没有消息,从下方输入框开始对话。</div>
      )}
      {messages.map((m) => (
        <Bubble key={m.id} msg={m} />
      ))}
    </div>
  )
}

function Bubble({ msg }: { msg: UiMessage }) {
  return (
    <div className={`bubble ${msg.role}`}>
      <div className="bubble-role">
        {msg.role === 'user' ? '你' : '助手'}
      </div>
      {msg.text && (
        <div className="bubble-text">
          {msg.text.split('\n').map((line, i) => (
            <p key={i}>{line || '\u00a0'}</p>
          ))}
        </div>
      )}
      {msg.toolCalls && msg.toolCalls.length > 0 && (
        <div className="tool-stack">
          {msg.toolCalls.map((tc) => (
            <ToolCard key={tc.id} call={tc} />
          ))}
        </div>
      )}
    </div>
  )
}

function ToolCard({ call }: { call: ToolCallCard }) {
  const [open, setOpen] = useState(false)
  const state =
    call.ok === undefined ? 'running' : call.ok ? 'ok' : 'err'
  return (
    <div className={`tool-card tool-${state}`}>
      <button
        className="tool-head"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        <span className="tool-glyph">{iconFor(call.name)}</span>
        <span className="tool-name">{call.name}</span>
        <span className="tool-state">
          {state === 'running' ? '执行中…' : state === 'ok' ? '✓' : '✗'}
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

function Composer({
  disabled,
  onSend,
  onClearSession,
}: {
  disabled: boolean
  onSend: (text: string) => void
  onClearSession?: () => void
}) {
  const [draft, setDraft] = useState('')
  const [recording, setRecording] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const imageInputRef = useRef<HTMLInputElement | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  const insertTool = (prefix: string) => {
    const ta = document.querySelector<HTMLTextAreaElement>('form.composer textarea')
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
    if (file.type.startsWith('text/') || file.name.match(/\.(json|md|py|js|ts|css|html|txt|csv|xml|yaml|yml)$/i)) {
      const text = await file.text()
      insertTool(`[文件: ${file.name}]\n${text.slice(0, 800)}${text.length > 800 ? '\n...(已截断)' : ''}`)
    } else {
      insertTool(`[附件: ${file.name}]`)
    }
  }

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      insertTool(`![${file.name}](${reader.result})`)
    }
    reader.readAsDataURL(file)
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
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          title="上传文件"
        >
          📎
        </button>
        <input ref={imageInputRef} type="file" accept="image/*" className="hidden-input" onChange={handleImageUpload} />
        <button
          type="button"
          className="composer-tool-btn"
          disabled={disabled}
          onClick={() => imageInputRef.current?.click()}
          title="上传图片"
        >
          🖼️
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
        <textarea
          rows={1}
          placeholder={disabled ? '先选择一个会话…' : '输入消息…'}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
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
        <button
          type="submit"
          className="composer-send-btn"
          disabled={disabled || !draft.trim()}
          title="发送"
        >
          ↑
        </button>
      </div>
    </form>
  )
}
