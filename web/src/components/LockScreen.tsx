import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

/**
 * 页面锁屏：设了「进入密码」且未解锁时，整个界面被它替换掉 —— 只有密码框，
 * 看不到侧栏、会话、内容（用户要求：锁了就该只看到输入框）。
 *
 * 解锁成功后回调 `onUnlocked` 让 App 挂载真正的界面（各视图此时才首次挂载，
 * 于是它们的首次请求都是带令牌的，不会撞上 423）。
 *
 * `checking` 是启动时的过渡态：先把界面藏住，等 `/access/status` 回来再决定，
 * 免得「先闪一下界面、再跳锁屏」把内容漏出去。
 */
export function LockScreen({
  mode,
  onUnlocked,
}: {
  mode: 'checking' | 'locked'
  onUnlocked: () => void
}) {
  const [pwd, setPwd] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [hasPassword, setHasPassword] = useState<boolean | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (mode !== 'locked') return
    void api
      .accessStatus()
      .then((s) => setHasPassword(s.hasPassword))
      .catch(() => setHasPassword(true))
    inputRef.current?.focus()
  }, [mode])

  const unlock = async () => {
    if (busy || !pwd) return
    setBusy(true)
    setError('')
    try {
      await api.accessUnlock(pwd)
      setPwd('')
      onUnlocked()
    } catch {
      setError('密码不正确')
      setPwd('')
      inputRef.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="lock-screen">
      <div className="lock-card">
        <div className="lock-icon">🔒</div>
        <div className="lock-title">已锁定</div>
        <div className="lock-sub">
          {mode === 'checking' ? '正在校验访问权限…' : '请输入进入密码'}
        </div>

        {mode === 'locked' && (
          <>
            <form
              className="lock-row"
              onSubmit={(e) => {
                e.preventDefault()
                void unlock()
              }}
            >
              <input
                ref={inputRef}
                type="password"
                value={pwd}
                autoFocus
                placeholder="进入密码"
                disabled={busy}
                onChange={(e) => {
                  setPwd(e.target.value)
                  setError('')
                }}
              />
              <button type="submit" disabled={busy || !pwd}>
                {busy ? '…' : '解锁'}
              </button>
            </form>
            {error && <div className="lock-err">{error}</div>}
            {hasPassword === false && (
              <div className="lock-hint">
                当前没有设置过进入密码。若这是你自己的服务，请在侧栏「👤 user」
                里设置；或点下方按钮直接进入。
              </div>
            )}
            {hasPassword === false && (
              <button className="lock-skip" onClick={onUnlocked}>
                直接进入
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
