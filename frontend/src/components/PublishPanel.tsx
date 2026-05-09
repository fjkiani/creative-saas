/**
 * PublishPanel — CreativeOS v4
 *
 * Publish to Instagram and TikTok with optional scheduling.
 * Shows publish results with post URLs.
 */
import React, { useState, useEffect } from 'react'

interface PublishResult {
  id: string
  platform: string
  market: string
  post_url: string | null
  post_id: string | null
  published_at: string | null
  status: string
  error: string | null
}

interface PublishPanelProps {
  runId: string
  runStatus: string
  apiUrl?: string
  apiKey?: string
}

export function PublishPanel({ runId, runStatus, apiUrl = '', apiKey = '' }: PublishPanelProps) {
  const [platforms, setPlatforms] = useState<string[]>([])
  const [scheduledTime, setScheduledTime] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [results, setResults] = useState<PublishResult[]>([])
  const [error, setError] = useState<string | null>(null)

  const headers = apiKey ? { 'X-Api-Key': apiKey } : {}

  useEffect(() => {
    if (runStatus === 'COMPLETE') {
      fetch(`${apiUrl}/api/runs/${runId}/publish`, { headers })
        .then(r => r.json())
        .then(data => setResults(Array.isArray(data) ? data : []))
        .catch(() => {})
    }
  }, [runId, runStatus])

  const togglePlatform = (platform: string) => {
    setPlatforms(prev =>
      prev.includes(platform)
        ? prev.filter(p => p !== platform)
        : [...prev, platform]
    )
  }

  const handlePublish = async () => {
    if (platforms.length === 0) return
    setPublishing(true)
    setError(null)

    try {
      const resp = await fetch(`${apiUrl}/api/runs/${runId}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({
          platforms,
          scheduled_time: scheduledTime || null,
        }),
      })

      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }

      // Poll for results
      const poll = setInterval(async () => {
        const r = await fetch(`${apiUrl}/api/runs/${runId}/publish`, { headers })
        if (r.ok) {
          const data = await r.json()
          if (data.length > results.length) {
            setResults(data)
            clearInterval(poll)
            setPublishing(false)
          }
        }
      }, 2000)

      setTimeout(() => {
        clearInterval(poll)
        setPublishing(false)
      }, 60000)

    } catch (e) {
      setError(String(e))
      setPublishing(false)
    }
  }

  if (runStatus !== 'COMPLETE') return null

  return (
    <div style={styles.container}>
      <div style={styles.title}>Publish</div>

      {/* Platform toggles */}
      <div style={styles.platforms}>
        {[
          { id: 'instagram', label: 'Instagram', icon: '📸', desc: 'Feed + Reels' },
          { id: 'tiktok', label: 'TikTok', icon: '🎵', desc: 'Videos' },
        ].map(p => (
          <button
            key={p.id}
            style={{
              ...styles.platformBtn,
              ...(platforms.includes(p.id) ? styles.platformBtnActive : {}),
            }}
            onClick={() => togglePlatform(p.id)}
          >
            <span style={styles.platformIcon}>{p.icon}</span>
            <div>
              <div style={styles.platformName}>{p.label}</div>
              <div style={styles.platformDesc}>{p.desc}</div>
            </div>
            {platforms.includes(p.id) && <span style={styles.checkmark}>✓</span>}
          </button>
        ))}
      </div>

      {/* Scheduling */}
      <div style={styles.scheduleRow}>
        <label style={styles.scheduleLabel}>Schedule (optional)</label>
        <input
          type="datetime-local"
          style={styles.dateInput}
          value={scheduledTime}
          onChange={e => setScheduledTime(e.target.value)}
        />
      </div>

      {error && <div style={styles.errorBox}>{error}</div>}

      {/* Publish button */}
      <button
        style={{
          ...styles.publishBtn,
          opacity: publishing || platforms.length === 0 ? 0.5 : 1,
          cursor: publishing || platforms.length === 0 ? 'not-allowed' : 'pointer',
        }}
        onClick={handlePublish}
        disabled={publishing || platforms.length === 0}
      >
        {publishing
          ? 'Publishing...'
          : scheduledTime
          ? `Schedule for ${new Date(scheduledTime).toLocaleString()}`
          : `Publish Now to ${platforms.join(' + ') || '...'}`}
      </button>

      {/* Results */}
      {results.length > 0 && (
        <div style={styles.results}>
          <div style={styles.resultsTitle}>Publish Results</div>
          {results.map((r, i) => (
            <div key={i} style={styles.resultRow}>
              <span style={styles.resultPlatform}>{r.platform}</span>
              <span style={styles.resultMarket}>{r.market}</span>
              <span style={{
                ...styles.resultStatus,
                color: r.status === 'published' ? '#22c55e'
                  : r.status === 'scheduled' ? '#f59e0b'
                  : '#ef4444',
              }}>
                {r.status}
              </span>
              {r.post_url && (
                <a
                  href={r.post_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={styles.postLink}
                >
                  View post →
                </a>
              )}
              {r.error && <span style={styles.resultError}>{r.error}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: '#1a1a1a',
    border: '1px solid #2a2a2a',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  title: { fontSize: 14, fontWeight: 600, color: '#e8e8e8' },
  platforms: { display: 'flex', gap: 8 },
  platformBtn: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 12px',
    background: '#222',
    border: '1px solid #333',
    borderRadius: 8,
    cursor: 'pointer',
    position: 'relative',
  },
  platformBtnActive: {
    border: '1px solid #1d4ed8',
    background: 'rgba(29,78,216,0.1)',
  },
  platformIcon: { fontSize: 20 },
  platformName: { fontSize: 12, fontWeight: 600, color: '#e8e8e8' },
  platformDesc: { fontSize: 10, color: '#666' },
  checkmark: {
    position: 'absolute',
    top: 6,
    right: 8,
    fontSize: 12,
    color: '#1d4ed8',
    fontWeight: 700,
  },
  scheduleRow: { display: 'flex', flexDirection: 'column', gap: 4 },
  scheduleLabel: { fontSize: 11, color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em' },
  dateInput: {
    background: '#111',
    border: '1px solid #333',
    color: '#e8e8e8',
    borderRadius: 6,
    padding: '7px 10px',
    fontSize: 12,
    outline: 'none',
  },
  errorBox: {
    background: '#1f0a0a',
    border: '1px solid #7f1d1d',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 12,
    color: '#fca5a5',
  },
  publishBtn: {
    width: '100%',
    padding: '11px 16px',
    background: 'linear-gradient(135deg, #059669, #0d9488)',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  results: { display: 'flex', flexDirection: 'column', gap: 6 },
  resultsTitle: { fontSize: 11, color: '#888', fontWeight: 600 },
  resultRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 10px',
    background: '#222',
    borderRadius: 6,
    flexWrap: 'wrap',
  },
  resultPlatform: { fontSize: 11, fontWeight: 600, color: '#ccc', minWidth: 70 },
  resultMarket: { fontSize: 10, color: '#666' },
  resultStatus: { fontSize: 10, fontWeight: 700 },
  postLink: { fontSize: 11, color: '#60a5fa', textDecoration: 'none', marginLeft: 'auto' },
  resultError: { fontSize: 10, color: '#fca5a5', flex: 1 },
}
