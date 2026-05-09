/**
 * VideoPlayer — CreativeOS v4
 *
 * Shows generated videos grouped by aspect ratio.
 * Allows triggering video generation (slideshow or AI mode).
 */
import React, { useState, useEffect } from 'react'

interface VideoOutput {
  id: string
  ratio: string
  mode: string
  storage_url: string
  duration_s: number
}

interface VideoPlayerProps {
  runId: string
  runStatus: string
  apiUrl?: string
  apiKey?: string
}

export function VideoPlayer({ runId, runStatus, apiUrl = '', apiKey = '' }: VideoPlayerProps) {
  const [videos, setVideos] = useState<VideoOutput[]>([])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [selectedMode, setSelectedMode] = useState<'slideshow' | 'ai'>('slideshow')
  const [activeVideo, setActiveVideo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const headers = apiKey ? { 'X-Api-Key': apiKey } : {}

  const fetchVideos = async () => {
    setLoading(true)
    try {
      const resp = await fetch(`${apiUrl}/api/runs/${runId}/videos`, { headers })
      if (resp.ok) {
        const data = await resp.json()
        setVideos(data)
        if (data.length > 0 && !activeVideo) {
          setActiveVideo(data[0].storage_url)
        }
      }
    } catch (e) {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (runStatus === 'COMPLETE') {
      fetchVideos()
    }
  }, [runId, runStatus])

  const handleGenerate = async () => {
    setGenerating(true)
    setError(null)
    try {
      const resp = await fetch(`${apiUrl}/api/runs/${runId}/videos/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ mode: selectedMode }),
      })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      // Poll for completion
      const poll = setInterval(async () => {
        const vids = await fetch(`${apiUrl}/api/runs/${runId}/videos`, { headers })
        if (vids.ok) {
          const data = await vids.json()
          if (data.length > videos.length) {
            setVideos(data)
            setActiveVideo(data[0]?.storage_url || null)
            clearInterval(poll)
            setGenerating(false)
          }
        }
      }, 3000)
      // Stop polling after 3 minutes
      setTimeout(() => {
        clearInterval(poll)
        setGenerating(false)
      }, 180000)
    } catch (e) {
      setError(String(e))
      setGenerating(false)
    }
  }

  if (runStatus !== 'COMPLETE') {
    return null
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.title}>
          Video Trailers
          {videos.length > 0 && (
            <span style={styles.badge}>{videos.length} videos</span>
          )}
        </div>
        <div style={styles.controls}>
          <select
            style={styles.select}
            value={selectedMode}
            onChange={e => setSelectedMode(e.target.value as 'slideshow' | 'ai')}
            disabled={generating}
          >
            <option value="slideshow">Slideshow (free, ~10s)</option>
            <option value="ai">AI Motion (premium, ~2min)</option>
          </select>
          <button
            style={{
              ...styles.generateBtn,
              opacity: generating ? 0.5 : 1,
              cursor: generating ? 'not-allowed' : 'pointer',
            }}
            onClick={handleGenerate}
            disabled={generating}
          >
            {generating ? 'Generating...' : videos.length > 0 ? '↺ Regenerate' : '▶ Generate Video'}
          </button>
        </div>
      </div>

      {error && <div style={styles.errorBox}>{error}</div>}

      {generating && (
        <div style={styles.generatingBanner}>
          <div style={styles.spinner}>⟳</div>
          Generating {selectedMode} video — this may take a moment...
        </div>
      )}

      {videos.length > 0 && (
        <div style={styles.playerLayout}>
          {/* Main video */}
          <div style={styles.mainPlayer}>
            {activeVideo && (
              <video
                key={activeVideo}
                src={activeVideo}
                controls
                autoPlay
                loop
                style={styles.video}
              />
            )}
          </div>

          {/* Ratio selector */}
          <div style={styles.ratioList}>
            {videos.map(v => (
              <button
                key={v.id}
                style={{
                  ...styles.ratioBtn,
                  ...(activeVideo === v.storage_url ? styles.ratioBtnActive : {}),
                }}
                onClick={() => setActiveVideo(v.storage_url)}
              >
                <div style={styles.ratioBadge}>{v.ratio}</div>
                <div style={styles.ratioMeta}>
                  {v.mode} · {v.duration_s.toFixed(1)}s
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {!loading && !generating && videos.length === 0 && (
        <div style={styles.emptyState}>
          No videos yet. Click "Generate Video" to create trailers from your assets.
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
    gap: 16,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 10,
  },
  title: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8e8e8',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  badge: {
    fontSize: 10,
    color: '#888',
    background: '#2a2a2a',
    padding: '2px 8px',
    borderRadius: 20,
  },
  controls: { display: 'flex', gap: 8, alignItems: 'center' },
  select: {
    background: '#111',
    border: '1px solid #333',
    color: '#e8e8e8',
    borderRadius: 6,
    padding: '6px 10px',
    fontSize: 12,
    outline: 'none',
  },
  generateBtn: {
    padding: '7px 14px',
    background: '#1d4ed8',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
  },
  errorBox: {
    background: '#1f0a0a',
    border: '1px solid #7f1d1d',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 12,
    color: '#fca5a5',
  },
  generatingBanner: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    background: 'rgba(29,78,216,0.1)',
    border: '1px solid rgba(29,78,216,0.3)',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 12,
    color: '#93c5fd',
  },
  spinner: { fontSize: 16, animation: 'spin 1s linear infinite' },
  playerLayout: {
    display: 'grid',
    gridTemplateColumns: '1fr 120px',
    gap: 12,
    alignItems: 'start',
  },
  mainPlayer: {
    background: '#111',
    borderRadius: 8,
    overflow: 'hidden',
  },
  video: {
    width: '100%',
    display: 'block',
    maxHeight: 400,
  },
  ratioList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  ratioBtn: {
    padding: '8px 10px',
    background: '#222',
    border: '1px solid #333',
    borderRadius: 8,
    cursor: 'pointer',
    textAlign: 'left',
  },
  ratioBtnActive: {
    border: '1px solid #1d4ed8',
    background: 'rgba(29,78,216,0.1)',
  },
  ratioBadge: {
    fontSize: 12,
    fontWeight: 700,
    color: '#e8e8e8',
  },
  ratioMeta: {
    fontSize: 10,
    color: '#666',
    marginTop: 2,
  },
  emptyState: {
    textAlign: 'center',
    color: '#555',
    fontSize: 12,
    padding: '24px 0',
  },
}
