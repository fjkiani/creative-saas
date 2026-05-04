import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import yaml from 'js-yaml'
import { BriefEditor } from '../components/BriefEditor'

const LLM_PROVIDERS = [
  { value: 'gemini',    label: 'Gemini 2.5 Pro (default)' },
  { value: 'openai',   label: 'GPT-4o' },
  { value: 'anthropic',label: 'Claude 3.5 Sonnet' },
]

const IMAGE_PROVIDERS = [
  { value: 'gemini',    label: 'Imagen 3 / Gemini (default)' },
  { value: 'openai',   label: 'DALL-E 3' },
  { value: 'firefly',  label: 'Adobe Firefly Image5' },
  { value: 'stability',label: 'Stable Diffusion 3.5' },
]

export function NewCampaign() {
  const navigate = useNavigate()
  const [briefRaw, setBriefRaw] = useState('')
  const [briefParsed, setBriefParsed] = useState<Record<string, unknown> | null>(null)
  const [llmProvider, setLlmProvider] = useState('')
  const [imageProvider, setImageProvider] = useState('')
  const [examples, setExamples] = useState<Record<string, unknown>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load example briefs from backend
  useEffect(() => {
    fetch('/api/briefs/examples')
      .then(r => r.json())
      .then(data => {
        setExamples(data)
        // Auto-load first example
        const first = Object.values(data)[0]
        if (first) {
          const raw = yaml.dump(first, { indent: 2 })
          setBriefRaw(raw)
          setBriefParsed(first as Record<string, unknown>)
        }
      })
      .catch(() => {})
  }, [])

  const handleSubmit = async () => {
    if (!briefParsed) {
      setError('Please provide a valid campaign brief.')
      return
    }
    setSubmitting(true)
    setError(null)

    try {
      const res = await fetch('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brief: briefParsed,
          llm_provider: llmProvider || undefined,
          image_provider: imageProvider || undefined,
        }),
      })

      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || `HTTP ${res.status}`)
      }

      const data = await res.json()
      navigate(`/runs/${data.run_id}`)
    } catch (e) {
      setError(String(e))
      setSubmitting(false)
    }
  }

  return (
    <div style={styles.page}>
      <div style={styles.container}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.logo}>⚡</div>
          <div>
            <h1 style={styles.title}>Creative Automation Pipeline</h1>
            <p style={styles.subtitle}>
              GenAI-powered social ad creative generation · LangGraph · Supabase Realtime
            </p>
          </div>
        </div>

        <div style={styles.grid}>
          {/* Left: Brief editor */}
          <div style={styles.leftCol}>
            <BriefEditor
              value={briefRaw}
              onChange={(raw, parsed) => { setBriefRaw(raw); setBriefParsed(parsed) }}
              examples={examples}
            />
          </div>

          {/* Right: Config + submit */}
          <div style={styles.rightCol}>
            {/* Provider config */}
            <div style={styles.card}>
              <div style={styles.cardTitle}>Provider Configuration</div>
              <p style={styles.cardSubtitle}>
                Override defaults via env vars. Swap providers without code changes.
              </p>

              <div style={styles.field}>
                <label style={styles.fieldLabel}>LLM Provider</label>
                <select
                  style={styles.select}
                  value={llmProvider}
                  onChange={e => setLlmProvider(e.target.value)}
                >
                  <option value="">Use default (env: LLM_PROVIDER)</option>
                  {LLM_PROVIDERS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>

              <div style={styles.field}>
                <label style={styles.fieldLabel}>Image Provider</label>
                <select
                  style={styles.select}
                  value={imageProvider}
                  onChange={e => setImageProvider(e.target.value)}
                >
                  <option value="">Use default (env: IMAGE_PROVIDER)</option>
                  {IMAGE_PROVIDERS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Pipeline overview */}
            <div style={styles.card}>
              <div style={styles.cardTitle}>Pipeline Stages</div>
              {[
                ['1', 'Brief Enrichment', 'LLM → CreativeSpec'],
                ['2', 'Prompt Generation', 'Per product × market'],
                ['3', 'Pre-flight Compliance', 'Legal + brand check'],
                ['4', 'Image Generation', 'Cache → Generate'],
                ['5', 'Compositing', '3 aspect ratios'],
                ['6', 'Localization', 'LLM copy adaptation'],
                ['7', 'Post Compliance', 'Logo + color + text'],
              ].map(([num, name, desc]) => (
                <div key={num} style={styles.stageRow}>
                  <div style={styles.stageNum}>{num}</div>
                  <div>
                    <div style={styles.stageName}>{name}</div>
                    <div style={styles.stageDesc}>{desc}</div>
                  </div>
                </div>
              ))}
            </div>

            {/* Error */}
            {error && (
              <div style={styles.errorBox}>
                <span>⚠</span> {error}
              </div>
            )}

            {/* Submit */}
            <button
              style={{
                ...styles.submitBtn,
                opacity: submitting || !briefParsed ? 0.5 : 1,
                cursor: submitting || !briefParsed ? 'not-allowed' : 'pointer',
              }}
              onClick={handleSubmit}
              disabled={submitting || !briefParsed}
            >
              {submitting ? 'Starting pipeline...' : 'Run Pipeline →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { minHeight: '100vh', padding: '32px 24px', background: '#0f0f0f' },
  container: { maxWidth: 1200, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 32 },
  header: { display: 'flex', alignItems: 'center', gap: 16 },
  logo: { fontSize: 40 },
  title: { fontSize: 24, fontWeight: 700, color: '#e8e8e8', margin: 0 },
  subtitle: { fontSize: 13, color: '#666', margin: '4px 0 0' },
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 380px',
    gap: 24,
    alignItems: 'start',
  },
  leftCol: { display: 'flex', flexDirection: 'column', gap: 16 },
  rightCol: { display: 'flex', flexDirection: 'column', gap: 16 },
  card: {
    background: '#1a1a1a', border: '1px solid #2a2a2a',
    borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 12,
  },
  cardTitle: { fontSize: 13, fontWeight: 600, color: '#e8e8e8' },
  cardSubtitle: { fontSize: 11, color: '#555', marginTop: -4 },
  field: { display: 'flex', flexDirection: 'column', gap: 4 },
  fieldLabel: { fontSize: 11, color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em' },
  select: {
    background: '#111', border: '1px solid #333', color: '#e8e8e8',
    borderRadius: 6, padding: '8px 10px', fontSize: 12, outline: 'none',
  },
  stageRow: { display: 'flex', gap: 10, alignItems: 'flex-start' },
  stageNum: {
    width: 22, height: 22, borderRadius: '50%', background: '#1d4ed8',
    color: '#fff', fontSize: 10, fontWeight: 700,
    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  stageName: { fontSize: 12, color: '#ccc', fontWeight: 500 },
  stageDesc: { fontSize: 10, color: '#555' },
  errorBox: {
    background: '#1f0a0a', border: '1px solid #7f1d1d',
    borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#fca5a5',
    display: 'flex', gap: 8,
  },
  submitBtn: {
    width: '100%', padding: '14px 24px',
    background: 'linear-gradient(135deg, #1d4ed8, #7c3aed)',
    color: '#fff', border: 'none', borderRadius: 10,
    fontSize: 15, fontWeight: 700, letterSpacing: '0.02em',
    transition: 'opacity 0.2s',
  },
}
