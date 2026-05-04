import React, { useState } from 'react'

interface ReviewCardProps {
  runId: string
  reviewScore: number | null
  preCompliance: {
    passed: boolean
    warnings: string[]
    errors: string[]
    issues: Array<{ severity: string; category: string; description: string }>
  } | null
  sampleAssets: Array<{ storage_url: string; product_id: string; market: string; aspect_ratio: string }>
  onReviewed: (decision: 'approve' | 'reject') => void
  apiKey?: string
  apiUrl?: string
}

export function ReviewCard({
  runId,
  reviewScore,
  preCompliance,
  sampleAssets,
  onReviewed,
  apiKey = '',
  apiUrl = '',
}: ReviewCardProps) {
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scorePercent = reviewScore !== null ? Math.round(reviewScore * 100) : null
  const scoreColor =
    reviewScore === null ? '#888'
    : reviewScore >= 0.85 ? '#22c55e'
    : reviewScore >= 0.60 ? '#f59e0b'
    : '#ef4444'

  const handleDecision = async (decision: 'approve' | 'reject') => {
    setSubmitting(true)
    setError(null)
    try {
      const base = apiUrl || import.meta.env.VITE_API_URL || 'http://localhost:8000'
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      const key = apiKey || import.meta.env.VITE_API_KEY || ''
      if (key) headers['X-Api-Key'] = key

      const res = await fetch(`${base}/api/runs/${runId}/review`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ decision, reviewer_notes: notes }),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }

      onReviewed(decision)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Review submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={styles.card}>
      {/* Header */}
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <div style={styles.badge}>⏸ PENDING REVIEW</div>
          <div style={styles.title}>Human Review Required</div>
        </div>
        {scorePercent !== null && (
          <div style={styles.scoreBlock}>
            <div style={{ ...styles.scoreValue, color: scoreColor }}>
              {scorePercent}%
            </div>
            <div style={styles.scoreLabel}>confidence</div>
          </div>
        )}
      </div>

      {/* Score explanation */}
      <div style={styles.scoreBar}>
        <div style={styles.scoreBarTrack}>
          <div
            style={{
              ...styles.scoreBarFill,
              width: `${scorePercent ?? 0}%`,
              background: scoreColor,
            }}
          />
          {/* Threshold markers */}
          <div style={{ ...styles.marker, left: '60%' }} title="Auto-reject threshold (0.60)" />
          <div style={{ ...styles.marker, left: '85%' }} title="Auto-approve threshold (0.85)" />
        </div>
        <div style={styles.scoreBarLabels}>
          <span style={{ color: '#ef4444' }}>Auto-reject</span>
          <span style={{ color: '#f59e0b' }}>Review band</span>
          <span style={{ color: '#22c55e' }}>Auto-approve</span>
        </div>
      </div>

      {/* Compliance issues */}
      {preCompliance && (preCompliance.warnings.length > 0 || preCompliance.errors.length > 0) && (
        <div style={styles.issuesSection}>
          <div style={styles.issuesTitle}>Compliance Flags</div>
          {preCompliance.issues.map((issue, i) => (
            <div key={i} style={{
              ...styles.issueRow,
              borderLeft: `3px solid ${issue.severity === 'ERROR' ? '#ef4444' : '#f59e0b'}`,
            }}>
              <span style={{
                ...styles.issueSeverity,
                color: issue.severity === 'ERROR' ? '#ef4444' : '#f59e0b',
              }}>
                {issue.severity}
              </span>
              <span style={styles.issueCategory}>[{issue.category}]</span>
              <span style={styles.issueDesc}>{issue.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* Sample asset previews */}
      {sampleAssets.length > 0 && (
        <div style={styles.assetsSection}>
          <div style={styles.assetsTitle}>Sample Composited Assets</div>
          <div style={styles.assetsGrid}>
            {sampleAssets.slice(0, 3).map((asset, i) => (
              <div key={i} style={styles.assetThumb}>
                <img
                  src={asset.storage_url}
                  alt={`${asset.product_id} ${asset.market}`}
                  style={styles.assetImg}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none'
                  }}
                />
                <div style={styles.assetMeta}>
                  {asset.product_id} · {asset.market} · {asset.aspect_ratio}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reviewer notes */}
      <div style={styles.notesSection}>
        <label style={styles.notesLabel}>Reviewer Notes (optional)</label>
        <textarea
          style={styles.notesInput}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Add notes for the audit trail..."
          rows={2}
          disabled={submitting}
        />
      </div>

      {/* Error */}
      {error && <div style={styles.errorMsg}>{error}</div>}

      {/* Action buttons */}
      <div style={styles.actions}>
        <button
          style={{ ...styles.btn, ...styles.rejectBtn }}
          onClick={() => handleDecision('reject')}
          disabled={submitting}
        >
          {submitting ? '...' : '✕ Reject'}
        </button>
        <button
          style={{ ...styles.btn, ...styles.approveBtn }}
          onClick={() => handleDecision('approve')}
          disabled={submitting}
        >
          {submitting ? '...' : '✓ Approve & Continue'}
        </button>
      </div>

      <div style={styles.footer}>
        Pipeline paused at review gate · Decision is logged to the audit trail
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: '#1a1a1a',
    border: '1px solid #f59e0b',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  headerLeft: { display: 'flex', flexDirection: 'column', gap: 6 },
  badge: {
    fontSize: 10,
    fontWeight: 700,
    color: '#f59e0b',
    background: 'rgba(245,158,11,0.12)',
    padding: '3px 8px',
    borderRadius: 20,
    letterSpacing: '0.05em',
    width: 'fit-content',
  },
  title: { fontSize: 15, fontWeight: 700, color: '#e8e8e8' },
  scoreBlock: { textAlign: 'right' },
  scoreValue: { fontSize: 28, fontWeight: 800, lineHeight: 1 },
  scoreLabel: { fontSize: 10, color: '#666', marginTop: 2 },
  scoreBar: { display: 'flex', flexDirection: 'column', gap: 4 },
  scoreBarTrack: {
    height: 8,
    background: '#2a2a2a',
    borderRadius: 4,
    position: 'relative',
    overflow: 'visible',
  },
  scoreBarFill: {
    height: '100%',
    borderRadius: 4,
    transition: 'width 0.4s ease',
  },
  marker: {
    position: 'absolute',
    top: -3,
    width: 2,
    height: 14,
    background: '#444',
    borderRadius: 1,
  },
  scoreBarLabels: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 10,
    color: '#666',
  },
  issuesSection: { display: 'flex', flexDirection: 'column', gap: 6 },
  issuesTitle: { fontSize: 11, fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em' },
  issueRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'flex-start',
    padding: '6px 10px',
    background: '#111',
    borderRadius: 6,
    fontSize: 12,
  },
  issueSeverity: { fontWeight: 700, flexShrink: 0 },
  issueCategory: { color: '#666', flexShrink: 0 },
  issueDesc: { color: '#aaa' },
  assetsSection: { display: 'flex', flexDirection: 'column', gap: 8 },
  assetsTitle: { fontSize: 11, fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em' },
  assetsGrid: { display: 'flex', gap: 8 },
  assetThumb: { flex: 1, display: 'flex', flexDirection: 'column', gap: 4 },
  assetImg: {
    width: '100%',
    aspectRatio: '1',
    objectFit: 'cover',
    borderRadius: 6,
    background: '#111',
    border: '1px solid #2a2a2a',
  },
  assetMeta: { fontSize: 9, color: '#555', textAlign: 'center' },
  notesSection: { display: 'flex', flexDirection: 'column', gap: 6 },
  notesLabel: { fontSize: 11, color: '#666' },
  notesInput: {
    background: '#111',
    border: '1px solid #2a2a2a',
    borderRadius: 6,
    color: '#ccc',
    fontSize: 12,
    padding: '8px 10px',
    resize: 'vertical',
    fontFamily: 'inherit',
    outline: 'none',
  },
  errorMsg: {
    fontSize: 12,
    color: '#fca5a5',
    background: 'rgba(239,68,68,0.1)',
    padding: '8px 12px',
    borderRadius: 6,
  },
  actions: { display: 'flex', gap: 10 },
  btn: {
    flex: 1,
    padding: '10px 0',
    borderRadius: 8,
    border: 'none',
    fontSize: 13,
    fontWeight: 700,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  },
  rejectBtn: { background: '#2a1a1a', color: '#ef4444', border: '1px solid #ef4444' },
  approveBtn: { background: '#22c55e', color: '#000' },
  footer: { fontSize: 10, color: '#444', textAlign: 'center' },
}
