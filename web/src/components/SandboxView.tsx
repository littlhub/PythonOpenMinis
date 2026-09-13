import { useCallback, useEffect, useRef, useState } from 'react'
import { api, openSocket, type OpenSocketHandle } from '../api'
import type {
  ConsoleStatus,
  GuardEvent,
  GuardEventList,
  GuardLiveItem,
  GuardScope,
} from '../types'

/**
 * SandboxView — 沙箱控制台 + 守卫拦截面板
 *
 * 上半部分是终端：直接跑命令看输出（可设控制台密码，未解锁时不能执行）。
 * 下半部分是**拦截记录**：目录越界 / 异常删除 / 敏感信息被拦下的原始命令、调用目录、
 * 拦截原因与状态，每条都能手动放行（只放一次 / 本会话 / 永久白名单）。
 */
const SCOPE_LABEL: Record<GuardScope, string> = {
  once: '只放一次',
  session: '本会话',
  always: '永久白名单',
}

const FAMILY_ICON: Record<string, string> = { delete: '🗑️', secret: '🔑' }

export function SandboxView() {
  const [output, setOutput] = useState<string[]>([])
  const [command, setCommand] = useState('')
  const [running, setRunning] = useState(false)
  const wsRef = useRef<OpenSocketHandle | null>(null)
  const outRef = useRef<HTMLPreElement | null>(null)

  // 控制台密码
  const [console, setConsole] = useState<ConsoleStatus | null>(null)
  const [password, setPassword] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [pwdMode, setPwdMode] = useState<'none' | 'unlock' | 'set'>('none')
  const [authMsg, setAuthMsg] = useState('')

  // 拦截记录
  const [guard, setGuard] = useState<GuardEventList | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [scope, setScope] = useState<Record<string, GuardScope>>({})
  const [guardMsg, setGuardMsg] = useState('')

  // 实时检测流水（放行/拦截都显示，3 秒轮询）
  const [live, setLive] = useState<GuardLiveItem[]>([])

  const reloadLive = useCallback(async () => {
    try {
      const r = await api.guardLive()
      setLive(r.items)
    } catch {
      /* 流水读不到就保持原样 */
    }
  }, [])

  const reloadConsole = useCallback(async () => {
    try {
      setConsole(await api.consoleStatus())
    } catch {
      /* 状态读不到就按未锁定处理 */
    }
  }, [])

  const reloadGuard = useCallback(async () => {
    try {
      setGuard(await api.guardEvents())
    } catch (e) {
      setGuardMsg(String((e as Error).message))
    }
  }, [])

  useEffect(() => {
    const ws = openSocket((frame) => {
      if (frame.type === 'delta') {
        setOutput((prev) => [...prev, frame.text])
      } else if (frame.type === 'done') {
        setRunning(false)
        setOutput((prev) => [...prev, `\n[exit ${frame.exitCode ?? 0}]`])
        void reloadGuard()
        void reloadLive()
      } else if (frame.type === 'error') {
        setRunning(false)
        setOutput((prev) => [...prev, `error: ${frame.error}`])
        if ('locked' in frame && frame.locked) void reloadConsole()
        void reloadGuard()
        void reloadLive()
      }
    })
    wsRef.current = ws
    return () => ws.close()
  }, [reloadConsole, reloadGuard, reloadLive])

  useEffect(() => {
    outRef.current?.scrollTo({ top: outRef.current.scrollHeight })
  }, [output])

  useEffect(() => {
    void reloadConsole()
    void reloadGuard()
    void reloadLive()
    const t = setInterval(() => {
      void reloadGuard()
      void reloadLive()
    }, 3000)
    return () => clearInterval(t)
  }, [reloadConsole, reloadGuard, reloadLive])

  const run = useCallback(() => {
    const cmd = command.trim()
    if (!cmd || running) return
    setOutput((prev) => [...prev, `$ ${cmd}`])
    setCommand('')
    setRunning(true)
    wsRef.current?.send(JSON.stringify({ type: 'shell', command: cmd }))
  }, [command, running])

  const submitUnlock = async () => {
    setAuthMsg('')
    try {
      await api.consoleUnlock(password)
      setPassword('')
      setPwdMode('none')
      await reloadConsole()
      setOutput((prev) => [...prev, '\n[控制台已解锁]\n'])
    } catch (e) {
      setAuthMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const submitSetPassword = async () => {
    setAuthMsg('')
    try {
      await api.consoleSetPassword(password, currentPassword)
      setPassword('')
      setCurrentPassword('')
      setPwdMode('none')
      await reloadConsole()
    } catch (e) {
      setAuthMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const lockConsole = async () => {
    try {
      await api.consoleLock()
      await reloadConsole()
    } catch (e) {
      setAuthMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const doAllow = async (event: GuardEvent) => {
    const chosen = scope[event.id] ?? 'once'
    try {
      await api.guardAllow(event.id, chosen)
      setGuardMsg(`已放行（${SCOPE_LABEL[chosen]}）：${event.reasons[0] ?? event.id}`)
      await reloadGuard()
    } catch (e) {
      setGuardMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const doDeny = async (event: GuardEvent) => {
    try {
      await api.guardDeny(event.id)
      await reloadGuard()
    } catch (e) {
      setGuardMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const locked = console?.locked ?? false

  return (
    <div className="view sandbox">
      {/* ── 控制台 ───────────────────────────────────────────── */}
      <div className="sb-head">
        <span className="sb-title">🖥️ 沙箱控制台</span>
        {console?.hasPassword ? (
          <span className={`sb-lock ${locked ? 'locked' : 'open'}`}>
            {locked ? '🔒 已锁定' : '🔓 已解锁'}
          </span>
        ) : (
          <span className="sb-lock">未设密码</span>
        )}
        <div className="sb-head-actions">
          {console?.hasPassword && locked && (
            <button onClick={() => setPwdMode(pwdMode === 'unlock' ? 'none' : 'unlock')}>
              解锁
            </button>
          )}
          {console?.hasPassword && !locked && (
            <button onClick={() => void lockConsole()}>立即上锁</button>
          )}
          <button onClick={() => setPwdMode(pwdMode === 'set' ? 'none' : 'set')}>
            {console?.hasPassword ? '修改密码' : '设置控制台密码'}
          </button>
        </div>
      </div>

      {(pwdMode === 'unlock' || pwdMode === 'set') && (
        <div className="sb-auth">
          {pwdMode === 'set' && console?.hasPassword && (
            <input
              type="password"
              placeholder="当前密码"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          )}
          <input
            type="password"
            placeholder={pwdMode === 'set' ? '新密码（留空=取消密码）' : '控制台密码'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return
              void (pwdMode === 'set' ? submitSetPassword() : submitUnlock())
            }}
          />
          <button onClick={() => void (pwdMode === 'set' ? submitSetPassword() : submitUnlock())}>
            {pwdMode === 'set' ? '保存' : '解锁'}
          </button>
          {authMsg && <span className="sb-err">{authMsg}</span>}
        </div>
      )}

      <pre className="terminal" ref={outRef}>
        {locked
          ? '🔒 控制台已锁定：先输入控制台密码解锁，才能执行命令。'
          : output.length === 0
            ? 'OpenMinis sandbox — run a command below.'
            : output.join('')}
      </pre>
      <div className="composer">
        <span className="prompt">$</span>
        <input
          value={command}
          placeholder={locked ? '控制台已锁定' : 'ls -la'}
          disabled={locked}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
        />
        <button onClick={run} disabled={locked || running || !command.trim()}>
          Run
        </button>
      </div>

      {/* ── 实时检测流水 ───────────────────────────────────────── */}
      <div className="sb-head sb-head--guard">
        <span className="sb-title">📡 实时检测</span>
        <span className="sb-lock">
          {live.length > 0 ? `最近 ${live.length} 条 · 3 秒自动刷新` : '等待命令…'}
        </span>
      </div>
      <div className="sb-live">
        {live.length === 0 ? (
          <div className="sb-empty">
            agent 或控制台每执行一条命令，这里就实时显示一条检测结果（放行/拦截）。
          </div>
        ) : (
          live.map((it, i) => (
            <div key={`${it.time}-${i}`} className={`sb-live-row ${it.blocked ? 'blocked' : 'ok'}`}>
              <span className="sb-live-time">
                {new Date(it.time * 1000).toLocaleTimeString()}
              </span>
              <span className="sb-live-verdict">
                {it.blocked
                  ? `⛔ ${(FAMILY_ICON[it.family] ?? '🚫')}${
                      it.family === 'delete'
                        ? '异常删除'
                        : it.family === 'escape'
                          ? '目录越界'
                          : it.family === 'secret'
                            ? '敏感信息'
                            : '拦截'
                    }`
                  : '✅ 放行'}
              </span>
              <span className="sb-live-cmd" title={it.command}>
                {it.command.length > 90 ? it.command.slice(0, 90) + '…' : it.command}
              </span>
              {it.blocked && it.reasons.length > 0 && (
                <span className="sb-live-reasons" title={it.reasons.join('；')}>
                  {it.reasons[0].length > 40 ? it.reasons[0].slice(0, 40) + '…' : it.reasons[0]}
                </span>
              )}
            </div>
          ))
        )}
      </div>

      {/* ── 拦截记录 ─────────────────────────────────────────── */}
      <div className="sb-head sb-head--guard">
        <span className="sb-title">🛡️ 拦截状态</span>
        <span className="sb-lock">
          🚪 目录越界 {guard?.counts.escape ?? 0} · 🗑️ 异常删除 {guard?.counts.delete ?? 0} · 🔑 敏感信息 {guard?.counts.secret ?? 0}
          {guard?.blocked ? ` · 待处理 ${guard.blocked}` : ''}
        </span>
        <div className="sb-head-actions">
          <button onClick={() => void reloadGuard()}>刷新</button>
          <button
            onClick={() => {
              void api.guardClear().then(reloadGuard)
            }}
          >
            清空记录
          </button>
        </div>
      </div>
      {guardMsg && <div className="sb-msg">{guardMsg}</div>}

      <div className="sb-events">
        {!guard || guard.events.length === 0 ? (
          <div className="sb-empty">
            暂无拦截。被拦下的目录越界 / 异常删除 / 敏感信息会在这里显示原始命令、调用目录和放行入口。
          </div>
        ) : (
          guard.events.map((e) => (
            <div key={e.id} className={`sb-event ${e.status}`}>
              <button
                className="sb-event-head"
                onClick={() => setExpanded(expanded === e.id ? null : e.id)}
              >
                <span className="caret">{expanded === e.id ? '⌄' : '›'}</span>
                <span className="sb-event-icon">{FAMILY_ICON[e.family] ?? '🛡️'}</span>
                <span className="sb-event-title">
                  {e.reasons[0] ?? '命中守卫规则'}
                </span>
                <span className={`sb-badge ${e.status}`}>
                  {e.status === 'allowed'
                    ? `已放行${e.scope ? `（${SCOPE_LABEL[e.scope as GuardScope] ?? e.scope}）` : ''}`
                    : '已拦截'}
                </span>
                <span className="sb-event-time">
                  {new Date(e.time * 1000).toLocaleString()}
                </span>
              </button>

              {expanded === e.id && (
                <div className="sb-event-body">
                  <div className="sb-kv">
                    <span className="sb-k">类型</span>
                    <span>{e.familyLabel}</span>
                  </div>
                  <div className="sb-kv">
                    <span className="sb-k">调用目录</span>
                    <code>{e.cwd || '（未知）'}</code>
                  </div>
                  {e.targets.length > 0 && (
                    <div className="sb-kv">
                      <span className="sb-k">目标路径</span>
                      <code>{e.targets.join('、')}</code>
                    </div>
                  )}
                  <div className="sb-kv">
                    <span className="sb-k">来源</span>
                    <span>
                      {e.tool}
                      {e.session_id ? ` · 会话 ${e.session_id}` : ''}
                    </span>
                  </div>
                  <div className="sb-kv sb-kv--col">
                    <span className="sb-k">异常输出</span>
                    <pre className="sb-out">{e.output || '（无）'}</pre>
                  </div>
                  <div className="sb-kv sb-kv--col">
                    <span className="sb-k">拦截原因</span>
                    <ul className="sb-reasons">
                      {e.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="sb-event-actions">
                    <select
                      value={scope[e.id] ?? 'once'}
                      onChange={(ev) =>
                        setScope((prev) => ({
                          ...prev,
                          [e.id]: ev.target.value as GuardScope,
                        }))
                      }
                    >
                      {(guard.scopes as GuardScope[]).map((s) => (
                        <option key={s} value={s}>
                          {SCOPE_LABEL[s] ?? s}
                        </option>
                      ))}
                    </select>
                    <button className="primary" onClick={() => void doAllow(e)}>
                      放行
                    </button>
                    <button onClick={() => void doDeny(e)}>维持拦截</button>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
