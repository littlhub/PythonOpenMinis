import { useEffect, useState } from 'react'
import { api } from '../api'
import type { HealthInfo } from '../types'

/**
 * Mirrors ui/sessions/SessionListScreen.kt. The data layer isn't ported yet,
 * so this view surfaces backend health instead of a fake session list.
 */
export function SessionsView() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="view sessions">
      <h2>Sessions</h2>
      {error && <p className="error">{error}</p>}
      {health ? (
        <dl className="facts">
          <dt>status</dt>
          <dd>{health.status}</dd>
          <dt>data dir</dt>
          <dd className="mono">{health.data_dir}</dd>
          <dt>workspace</dt>
          <dd className="mono">{health.workspace}</dd>
        </dl>
      ) : (
        <p className="empty">Connecting…</p>
      )}
      <p className="empty">
        Session history arrives with the <code>openminis.data</code> port (Room → SQLAlchemy).
      </p>
    </div>
  )
}
