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

/** 把附件引用从正文里摘出来，正文只留用户真正打的字。 */
function splitAttachments(text: string): {
  text: string
  images: ImageRef[]
  files: FileRef[]
} {
  const images: ImageRef[] = []
  const toRef = (alt: string, src: string): ImageRef => {
    const ref: ImageRef = { alt: alt || '图片', src }
    if (/^https?:\/\//.test(src)) ref.url = src
    else if (!src.startsWith('data:')) {
      const base = src.split(/[\\/]/).pop()
      if (base) ref.url = `/api/upload/raw?name=${encodeURIComponent(base)}`
    }
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
        <MessageList
          messages={messages}
          sessionId={activeSessionId}
          onDeleted={(mid) =>
            setMessages((prev) => prev.filter((m) => m.id !== mid))
          }
        />
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
function MessageList({
  messages,
  sessionId,
  onDeleted,
}: {
  messages: UiMessage[]
  sessionId: string | null
  onDeleted: (messageId: string) => void
}) {
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
        <Bubble key={m.id} msg={m} sessionId={sessionId} onDeleted={onDeleted} />
      ))}
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
  return (
    <div className={`bubble ${msg.role}`}>
      <div className="bubble-head">
        <div className="bubble-role">
          {msg.role === 'user' ? '你' : '助手'}
        </div>
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
      </div>
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
                  title={img.src}
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
  const [attachBusy, setAttachBusy] = useState(false)
  const [attachError, setAttachError] = useState<string | null>(null)
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
        <textarea
          rows={1}
          placeholder={
            disabled
              ? '先选择一个会话…'
              : attachBusy
                ? '上传中…'
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
