/** 一次性回归脚本：验证 WebSocket 断线重连后消息能真的发出去。
 *
 *  老的 bug：重连出来的新 socket 没写回闭包里的 ``ws`` —— 重连后
 *  handle.send 仍然盯着已经死掉的那把，消息全卡在队列里发不出去，
 *  用户只能刷新页面。这个脚本把它钉死。
 */
type WSAny = Record<string, unknown>

class FakeWS {
  static instances: FakeWS[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  url: string
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWS.instances.push(this)
  }
  send(d: string) {
    if (this.readyState !== 1) throw new Error('send on closed socket')
    this.sent.push(d)
  }
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }
  _open() {
    this.readyState = 1
    this.onopen?.()
  }
  _drop() {
    this.readyState = 3
    this.onclose?.({ code: 1006 })
  }
}

const listeners: Record<string, (() => void)[]> = {}
;(globalThis as WSAny).WebSocket = FakeWS
;(globalThis as WSAny).location = { protocol: 'http:', host: '127.0.0.1:8765' }
;(globalThis as WSAny).window = {
  addEventListener: (t: string, f: () => void) => {
    listeners[t] = [...(listeners[t] ?? []), f]
  },
  removeEventListener: () => {},
  dispatchEvent: () => {},
}
;(globalThis as WSAny).document = {
  visibilityState: 'visible',
  addEventListener: (t: string, f: () => void) => {
    listeners[t] = [...(listeners[t] ?? []), f]
  },
  removeEventListener: () => {},
}
;(globalThis as WSAny).CustomEvent = class {
  constructor(public type: string, public init?: unknown) {}
}

const { openSocket } = await import('../src/api.ts')

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
let failed = 0
function check(name: string, ok: boolean, extra = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  if (!ok) failed++
}

const states: string[] = []
let reconnects = 0
const handle = openSocket(
  () => {},
  {
    onStateChange: (s) => states.push(s),
    onReconnect: () => {
      reconnects++
    },
  },
)

const first = FakeWS.instances[0]
check('首次连接创建了 socket', !!first)
first._open()
check('open 后 readyState=OPEN', handle.readyState === FakeWS.OPEN)

handle.send('A')
check('连接正常时直接发出', first.sent.join(',') === 'A', first.sent.join(','))

// 断线：应该先排队，然后自动重连
first._drop()
handle.send('B')
check('断线期间消息进队列（不抛错、也不丢）', true)
check('断线后 readyState 不是 OPEN', handle.readyState !== FakeWS.OPEN)

await sleep(1300) // 等 1s 退避重连
const second = FakeWS.instances[1]
check('退避后自动重连（新 socket）', !!second)
second!._open()
check('重连回调触发一次', reconnects === 1, `reconnects=${reconnects}`)
check('重连后 readyState=OPEN', handle.readyState === FakeWS.OPEN)
check('排队的那条被补发', second!.sent.join(',') === 'B', second!.sent.join(','))

// 关键断言：重连之后新消息必须走新 socket（老 bug 会塞进旧 socket 的队列）
handle.send('C')
check(
  '重连后新消息发到新连接',
  second!.sent.join(',') === 'B,C',
  second!.sent.join(','),
)
check(
  '没有写进已经死掉的旧连接',
  first.sent.join(',') === 'A',
  first.sent.join(','),
)

handle.close()
check(
  'close() 后不再重连（状态收尾为 closed）',
  handle.state === 'closed' || handle.state === 'closing',
  handle.state,
)

console.log(failed === 0 ? '\nALL PASS' : `\n${failed} FAILED`)
process.exit(failed === 0 ? 0 : 1)
