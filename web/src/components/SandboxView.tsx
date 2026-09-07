import { useCallback, useEffect, useRef, useState } from 'react'
import { openSocket, type OpenSocketHandle } from '../api'
import type { ServerFrame } from '../types'

/** Mirrors the Android terminal surface (ui/terminal/**). */
export function SandboxView() {
  const [output, setOutput] = useState<string[]>([])
  const [command, setCommand] = useState('')
  const [running, setRunning] = useState(false)
  const wsRef = useRef<OpenSocketHandle | null>(null)
  const outRef = useRef<HTMLPreElement | null>(null)

  useEffect(() => {
    const ws = openSocket((frame: ServerFrame) => {
      if (frame.type === 'delta') {
        setOutput((prev) => [...prev, frame.text])
      } else if (frame.type === 'done') {
        setRunning(false)
        setOutput((prev) => [...prev, `\n[exit ${frame.exitCode ?? 0}]`])
      } else if (frame.type === 'error') {
        setRunning(false)
        setOutput((prev) => [...prev, `error: ${frame.error}`])
      }
    })
    wsRef.current = ws
    return () => ws.close()
  }, [])

  useEffect(() => {
    outRef.current?.scrollTo({ top: outRef.current.scrollHeight })
  }, [output])

  const run = useCallback(() => {
    const cmd = command.trim()
    if (!cmd || running) return
    setOutput((prev) => [...prev, `$ ${cmd}`])
    setCommand('')
    setRunning(true)
    wsRef.current?.send(JSON.stringify({ type: 'shell', command: cmd }))
  }, [command, running])

  return (
    <div className="view sandbox">
      <pre className="terminal" ref={outRef}>
        {output.length === 0 ? 'OpenMinis sandbox — run a command below.' : output.join('')}
      </pre>
      <div className="composer">
        <span className="prompt">$</span>
        <input
          value={command}
          placeholder="ls -la"
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
        />
        <button onClick={run} disabled={running || !command.trim()}>
          Run
        </button>
      </div>
    </div>
  )
}
