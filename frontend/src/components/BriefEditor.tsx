import React, { useState, useCallback } from 'react'
import yaml from 'js-yaml'

interface Props {
  value: string
  onChange: (value: string, parsed: Record<string, unknown> | null) => void
  examples?: Record<string, unknown>
}

export function BriefEditor({ value, onChange, examples }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'yaml' | 'json'>('yaml')

  const handleChange = useCallback((raw: string) => {
    try {
      const parsed = mode === 'yaml'
        ? yaml.load(raw) as Record<string, unknown>
        : JSON.parse(raw)
      setError(null)
      onChange(raw, parsed)
    } catch (e) {
      setError(String(e))
      onChange(raw, null)
    }
  }, [mode, onChange])

  const loadExample = (key: string) => {
    if (!examples?.[key]) return
    const raw = mode === 'yaml'
      ? yaml.dump(examples[key], { indent: 2 })
      : JSON.stringify(examples[key], null, 2)
    handleChange(raw)
  }

  const toggleMode = () => {
    const newMode = mode === 'yaml' ? 'json' : 'yaml'
    try {
      const parsed = mode === 'yaml'
        ? yaml.load(value) as Record<string, unknown>
        : JSON.parse(value)
      const converted = newMode === 'yaml'
        ? yaml.dump(parsed, { indent: 2 })
        : JSON.stringify(parsed, null, 2)
      setMode(newMode)
      onChange(converted, parsed)
    } catch {
      setMode(newMode)
    }
  }

  return (
    <div style={styles.container}>
      <div style={styles.toolbar}>
        <span style={styles.label}>Campaign Brief</span>
        <div style={styles.toolbarRight}>
          {examples && Object.keys(examples).map(key => (
            <button key={key} style={styles.exampleBtn} onClick={() => loadExample(key)}>
              Load: {key.replace(/_/g, ' ')}
            </button>
          ))}
          <button style={styles.modeBtn} onClick={toggleMode}>
            {mode.toUpperCase()}
          </button>
        </div>
      </div>

      <textarea
        style={{
          ...styles.editor,
          borderColor: error ? '#ef4444' : '#2a2a2a',
        }}
        value={value}
        onChange={e => handleChange(e.target.value)}
        spellCheck={false}
        placeholder={`Paste your campaign brief in ${mode.toUpperCase()} format...`}
      />

      {error && (
        <div style={styles.error}>
          <span style={styles.errorIcon}>⚠</span>
          {error}
        </div>
      )}

      {!error && value && (
        <div style={styles.valid}>✓ Valid {mode.toUpperCase()}</div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', gap: 8 },
  toolbar: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', flexWrap: 'wrap', gap: 8,
  },
  label: { fontSize: 13, fontWeight: 600, color: '#ccc' },
  toolbarRight: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  exampleBtn: {
    fontSize: 11, padding: '4px 10px', borderRadius: 6,
    border: '1px solid #333', background: 'transparent',
    color: '#888', cursor: 'pointer',
  },
  modeBtn: {
    fontSize: 11, padding: '4px 10px', borderRadius: 6,
    border: '1px solid #1d4ed8', background: '#1d4ed8',
    color: '#fff', cursor: 'pointer', fontWeight: 700,
  },
  editor: {
    width: '100%', minHeight: 320, padding: 16,
    background: '#111', color: '#e8e8e8',
    border: '1px solid #2a2a2a', borderRadius: 8,
    fontFamily: "'Fira Code', 'Cascadia Code', 'Consolas', monospace",
    fontSize: 12, lineHeight: 1.6, resize: 'vertical',
    outline: 'none', transition: 'border-color 0.2s',
  },
  error: {
    fontSize: 12, color: '#fca5a5', background: '#1f0a0a',
    border: '1px solid #7f1d1d', borderRadius: 6, padding: '8px 12px',
    display: 'flex', gap: 6, alignItems: 'flex-start',
  },
  errorIcon: { flexShrink: 0 },
  valid: { fontSize: 11, color: '#22c55e' },
}
