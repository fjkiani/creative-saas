/**
 * CompetitorUpload — CreativeOS v4
 *
 * Upload competitor ad screenshots or enter a social URL.
 * Triggers analysis and shows the counter-brief.
 * Counter-brief is injected into the campaign brief as style_hints.
 */
import React, { useState, useRef } from 'react'

interface CompetitorAnalysis {
  id: string
  emotional_tone: string
  claims_made: string[]
  strengths: string[]
  weaknesses: string[]
  counter_strategy: string
  style_hints: Record<string, string>
  color_palette: string[]
}

interface CompetitorUploadProps {
  onAnalysisComplete?: (styleHints: Record<string, string>, analysis: CompetitorAnalysis) => void
  apiUrl?: string
  apiKey?: string
  workspaceId?: string
}

export function CompetitorUpload({
  onAnalysisComplete,
  apiUrl = '',
  apiKey = '',
  workspaceId,
}: CompetitorUploadProps) {
  const [mode, setMode] = useState<'screenshot' | 'url'>('screenshot')
  const [screenshots, setScreenshots] = useState<string[]>([])  // base64
  const [screenshotPreviews, setScreenshotPreviews] = useState<string[]>([])
  const [competitorUrl, setCompetitorUrl] = useState('')
  const [brandContext, setBrandContext] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<CompetitorAnalysis | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const headers = apiKey ? { 'X-Api-Key': apiKey } : {}

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = ev => {
        const dataUrl = ev.target?.result as string
        const b64 = dataUrl.split(',')[1]
        setScreenshots(prev => [...prev, b64])
        setScreenshotPreviews(prev => [...prev, dataUrl])
      }
      reader.readAsDataURL(file)
    })
  }

  const removeScreenshot = (index: number) => {
    setScreenshots(prev => prev.filter((_, i) => i !== index))
    setScreenshotPreviews(prev => prev.filter((_, i) => i !== index))
  }

  const handleAnalyze = async () => {
    if (mode === 'screenshot' && screenshots.length === 0) return
    if (mode === 'url' && !competitorUrl.trim()) return

    setAnalyzing(true)
    setError(null)
    setAnalysis(null)

    try {
      const resp = await fetch(`${apiUrl}/api/competitor/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({
          screenshots_base64: mode === 'screenshot' ? screenshots : [],
          competitor_url: mode === 'url' ? competitorUrl : null,
          brand_context: brandContext,
          workspace_id: workspaceId,
        }),
      })

      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }

      const { analysis_id } = await resp.json()

      // Poll for result
      const poll = setInterval(async () => {
        const r = await fetch(`${apiUrl}/api/competitor/${analysis_id}`, { headers })
        if (r.ok) {
          const data = await r.json()
          if (data.counter_strategy && data.counter_strategy !== 'Analyzing...') {
            setAnalysis(data)
            clearInterval(poll)
            setAnalyzing(false)
            onAnalysisComplete?.(data.style_hints || {}, data)
          }
        }
      }, 2000)

      setTimeout(() => {
        clearInterval(poll)
        if (!analysis) {
          setError('Analysis timed out. Please try again.')
          setAnalyzing(false)
        }
      }, 120000)

    } catch (e) {
      setError(String(e))
      setAnalyzing(false)
    }
  }

  return (
    <div style={styles.container}>
      <button
        style={styles.toggleHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span style={styles.toggleIcon}>{expanded ? '▼' : '▶'}</span>
        <span style={styles.toggleTitle}>Competitor Analysis</span>
        <span style={styles.toggleBadge}>optional</span>
        {analysis && <span style={styles.doneTag}>✓ Counter-brief ready</span>}
      </button>

      {expanded && (
        <div style={styles.body}>
          <p style={styles.description}>
            Upload competitor ads to generate a counter-brief. Your campaign will be
            automatically differentiated from their strategy.
          </p>

          {/* Mode tabs */}
          <div style={styles.modeTabs}>
            <button
              style={{ ...styles.modeTab, ...(mode === 'screenshot' ? styles.modeTabActive : {}) }}
              onClick={() => setMode('screenshot')}
            >
              📸 Screenshot Upload
            </button>
            <button
              style={{ ...styles.modeTab, ...(mode === 'url' ? styles.modeTabActive : {}) }}
              onClick={() => setMode('url')}
            >
              🔗 Social URL
            </button>
          </div>

          {mode === 'screenshot' && (
            <div style={styles.uploadArea}>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                style={{ display: 'none' }}
                onChange={handleFileSelect}
              />
              <button
                style={styles.uploadBtn}
                onClick={() => fileInputRef.current?.click()}
              >
                + Add Screenshots (up to 5)
              </button>
              {screenshotPreviews.length > 0 && (
                <div style={styles.previews}>
                  {screenshotPreviews.map((src, i) => (
                    <div key={i} style={styles.previewItem}>
                      <img src={src} alt={`Screenshot ${i + 1}`} style={styles.previewImg} />
                      <button
                        style={styles.removeBtn}
                        onClick={() => removeScreenshot(i)}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {mode === 'url' && (
            <div style={styles.urlInput}>
              <input
                type="text"
                style={styles.input}
                placeholder="https://www.instagram.com/competitor/ or @handle"
                value={competitorUrl}
                onChange={e => setCompetitorUrl(e.target.value)}
              />
              <p style={styles.urlHint}>
                Requires APIFY_API_TOKEN. Analyzes last 12 posts.
              </p>
            </div>
          )}

          <div style={styles.contextRow}>
            <label style={styles.label}>Your brand context (optional)</label>
            <input
              type="text"
              style={styles.input}
              placeholder="e.g. Premium skincare brand targeting 25-40 women"
              value={brandContext}
              onChange={e => setBrandContext(e.target.value)}
            />
          </div>

          {error && <div style={styles.errorBox}>{error}</div>}

          <button
            style={{
              ...styles.analyzeBtn,
              opacity: analyzing || (mode === 'screenshot' ? screenshots.length === 0 : !competitorUrl.trim()) ? 0.5 : 1,
              cursor: analyzing ? 'not-allowed' : 'pointer',
            }}
            onClick={handleAnalyze}
            disabled={analyzing || (mode === 'screenshot' ? screenshots.length === 0 : !competitorUrl.trim())}
          >
            {analyzing ? 'Analyzing... (~15s)' : 'Analyze Competitor →'}
          </button>

          {/* Analysis result */}
          {analysis && (
            <div style={styles.analysisResult}>
              <div style={styles.resultTitle}>Counter-Brief Generated</div>

              <div style={styles.resultGrid}>
                <div style={styles.resultItem}>
                  <div style={styles.resultLabel}>Their tone</div>
                  <div style={styles.resultValue}>{analysis.emotional_tone}</div>
                </div>
                <div style={styles.resultItem}>
                  <div style={styles.resultLabel}>Their claims</div>
                  <div style={styles.resultValue}>{analysis.claims_made.slice(0, 3).join(', ')}</div>
                </div>
              </div>

              <div style={styles.counterStrategy}>
                <div style={styles.resultLabel}>Counter-strategy</div>
                <div style={styles.counterText}>{analysis.counter_strategy}</div>
              </div>

              <div style={styles.styleHints}>
                <div style={styles.resultLabel}>Style hints injected into brief</div>
                {Object.entries(analysis.style_hints || {}).map(([k, v]) => (
                  <div key={k} style={styles.hintRow}>
                    <span style={styles.hintKey}>{k}:</span>
                    <span style={styles.hintValue}>{v}</span>
                  </div>
                ))}
              </div>

              <div style={styles.successBanner}>
                ✓ Counter-brief will be applied to your campaign automatically
              </div>
            </div>
          )}
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
    overflow: 'hidden',
  },
  toggleHeader: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '14px 16px',
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    textAlign: 'left',
  },
  toggleIcon: { fontSize: 10, color: '#555' },
  toggleTitle: { fontSize: 13, fontWeight: 600, color: '#e8e8e8' },
  toggleBadge: {
    fontSize: 10,
    color: '#888',
    background: '#2a2a2a',
    padding: '2px 8px',
    borderRadius: 20,
  },
  doneTag: {
    fontSize: 10,
    color: '#22c55e',
    background: 'rgba(34,197,94,0.1)',
    padding: '2px 8px',
    borderRadius: 20,
    marginLeft: 'auto',
  },
  body: {
    padding: '0 16px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    borderTop: '1px solid #2a2a2a',
  },
  description: { fontSize: 12, color: '#666', margin: '12px 0 0' },
  modeTabs: { display: 'flex', gap: 6 },
  modeTab: {
    flex: 1,
    padding: '7px 12px',
    background: '#222',
    border: '1px solid #333',
    borderRadius: 6,
    color: '#888',
    fontSize: 11,
    cursor: 'pointer',
  },
  modeTabActive: {
    color: '#e8e8e8',
    border: '1px solid #1d4ed8',
    background: 'rgba(29,78,216,0.1)',
  },
  uploadArea: { display: 'flex', flexDirection: 'column', gap: 8 },
  uploadBtn: {
    padding: '8px 14px',
    background: '#222',
    border: '1px dashed #444',
    borderRadius: 8,
    color: '#888',
    fontSize: 12,
    cursor: 'pointer',
  },
  previews: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  previewItem: { position: 'relative' },
  previewImg: {
    width: 64,
    height: 64,
    objectFit: 'cover',
    borderRadius: 6,
    border: '1px solid #333',
  },
  removeBtn: {
    position: 'absolute',
    top: -6,
    right: -6,
    width: 18,
    height: 18,
    background: '#ef4444',
    border: 'none',
    borderRadius: '50%',
    color: '#fff',
    fontSize: 12,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    lineHeight: 1,
  },
  urlInput: { display: 'flex', flexDirection: 'column', gap: 4 },
  urlHint: { fontSize: 10, color: '#555', margin: 0 },
  contextRow: { display: 'flex', flexDirection: 'column', gap: 4 },
  label: { fontSize: 11, color: '#888' },
  input: {
    background: '#111',
    border: '1px solid #333',
    color: '#e8e8e8',
    borderRadius: 6,
    padding: '8px 10px',
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
  analyzeBtn: {
    width: '100%',
    padding: '10px 16px',
    background: 'linear-gradient(135deg, #7c3aed, #1d4ed8)',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  analysisResult: {
    background: '#111',
    border: '1px solid #2a2a2a',
    borderRadius: 8,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  resultTitle: { fontSize: 12, fontWeight: 600, color: '#e8e8e8' },
  resultGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 },
  resultItem: { display: 'flex', flexDirection: 'column', gap: 2 },
  resultLabel: { fontSize: 10, color: '#555', textTransform: 'uppercase', letterSpacing: '0.05em' },
  resultValue: { fontSize: 11, color: '#ccc' },
  counterStrategy: { display: 'flex', flexDirection: 'column', gap: 4 },
  counterText: {
    fontSize: 12,
    color: '#e8e8e8',
    background: 'rgba(124,58,237,0.1)',
    border: '1px solid rgba(124,58,237,0.3)',
    borderRadius: 6,
    padding: '8px 10px',
    lineHeight: 1.5,
  },
  styleHints: { display: 'flex', flexDirection: 'column', gap: 4 },
  hintRow: { display: 'flex', gap: 6, fontSize: 11 },
  hintKey: { color: '#555', minWidth: 100 },
  hintValue: { color: '#ccc' },
  successBanner: {
    background: 'rgba(34,197,94,0.1)',
    border: '1px solid rgba(34,197,94,0.3)',
    borderRadius: 6,
    padding: '8px 10px',
    fontSize: 11,
    color: '#22c55e',
  },
}
