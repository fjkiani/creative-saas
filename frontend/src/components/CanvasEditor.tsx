/**
 * CanvasEditor — CreativeOS v4
 *
 * Three editing modes:
 *   Mode 1 — Text instruction: type what to change, AI edits it (~5s)
 *   Mode 2 — Mask painting: brush over region, type what to replace (~10s)
 *   Mode 3 — Layer panel: instant layer visibility/swap (no AI)
 *
 * Uses HTML5 Canvas for mask painting.
 * Fabric.js-style layer panel showing base/gradient/logo/text layers.
 */
import React, { useState, useRef, useEffect, useCallback } from 'react'

interface Layer {
  name: 'base' | 'gradient' | 'logo' | 'text'
  label: string
  url: string | null
  visible: boolean
}

interface CanvasEditorProps {
  assetId: string
  assetUrl: string
  runId: string
  apiUrl?: string
  apiKey?: string
  onEditComplete?: (newUrl: string) => void
}

type EditMode = 'text' | 'mask' | 'layer'

export function CanvasEditor({
  assetId,
  assetUrl,
  runId,
  apiUrl = '',
  apiKey = '',
  onEditComplete,
}: CanvasEditorProps) {
  const [mode, setMode] = useState<EditMode>('text')
  const [instruction, setInstruction] = useState('')
  const [layers, setLayers] = useState<Layer[]>([])
  const [currentUrl, setCurrentUrl] = useState(assetUrl)
  const [isEditing, setIsEditing] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [applyToAll, setApplyToAll] = useState(false)
  const [brushSize, setBrushSize] = useState(30)
  const [isPainting, setIsPainting] = useState(false)
  const [editHistory, setEditHistory] = useState<Array<{ before: string; after: string; instruction: string }>>([])

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const maskCanvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  // Load layers on mount
  useEffect(() => {
    fetch(`${apiUrl}/api/assets/${assetId}/layers`, {
      headers: apiKey ? { 'X-Api-Key': apiKey } : {},
    })
      .then(r => r.json())
      .then(data => {
        const layerDefs: Layer[] = [
          { name: 'base', label: 'Background', url: data.layers?.base, visible: true },
          { name: 'gradient', label: 'Gradient Overlay', url: data.layers?.gradient, visible: true },
          { name: 'logo', label: 'Logo', url: data.layers?.logo, visible: true },
          { name: 'text', label: 'Headline / Text', url: data.layers?.text, visible: true },
        ]
        setLayers(layerDefs)
      })
      .catch(() => {})
  }, [assetId, apiUrl, apiKey])

  // Initialize mask canvas when mode switches to mask
  useEffect(() => {
    if (mode === 'mask' && maskCanvasRef.current && imgRef.current) {
      const canvas = maskCanvasRef.current
      const img = imgRef.current
      canvas.width = img.naturalWidth || img.width
      canvas.height = img.naturalHeight || img.height
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.fillStyle = 'black'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
      }
    }
  }, [mode])

  const clearMask = () => {
    const canvas = maskCanvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (ctx) {
      ctx.fillStyle = 'black'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }
  }

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (mode !== 'mask') return
    setIsPainting(true)
    paint(e)
  }

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isPainting || mode !== 'mask') return
    paint(e)
  }

  const handleMouseUp = () => setIsPainting(false)

  const paint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = maskCanvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    const x = (e.clientX - rect.left) * scaleX
    const y = (e.clientY - rect.top) * scaleY

    ctx.fillStyle = 'white'
    ctx.beginPath()
    ctx.arc(x, y, brushSize * scaleX, 0, Math.PI * 2)
    ctx.fill()
  }

  const getMaskBase64 = (): string => {
    const canvas = maskCanvasRef.current
    if (!canvas) return ''
    return canvas.toDataURL('image/png').split(',')[1]
  }

  const handleEdit = async () => {
    if (!instruction.trim()) return
    setIsEditing(true)
    setEditError(null)

    try {
      const body: Record<string, unknown> = {
        mode,
        instruction,
        apply_to_all_ratios: applyToAll,
      }

      if (mode === 'mask') {
        body.mask_base64 = getMaskBase64()
      }

      const resp = await fetch(`${apiUrl}/api/assets/${assetId}/edit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-Api-Key': apiKey } : {}),
        },
        body: JSON.stringify(body),
      })

      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }

      const data = await resp.json()
      const newUrl = data.after_url

      setEditHistory(prev => [
        { before: currentUrl, after: newUrl, instruction },
        ...prev.slice(0, 9),
      ])
      setCurrentUrl(newUrl)
      setInstruction('')
      if (mode === 'mask') clearMask()
      onEditComplete?.(newUrl)

    } catch (e) {
      setEditError(String(e))
    } finally {
      setIsEditing(false)
    }
  }

  const handleUndo = () => {
    if (editHistory.length === 0) return
    const last = editHistory[0]
    setCurrentUrl(last.before)
    setEditHistory(prev => prev.slice(1))
  }

  return (
    <div style={styles.container}>
      {/* Mode tabs */}
      <div style={styles.modeTabs}>
        {(['text', 'mask', 'layer'] as EditMode[]).map(m => (
          <button
            key={m}
            style={{
              ...styles.modeTab,
              ...(mode === m ? styles.modeTabActive : {}),
            }}
            onClick={() => setMode(m)}
          >
            {m === 'text' ? '✏️ Text Edit' : m === 'mask' ? '🖌️ Mask Paint' : '⬛ Layers'}
          </button>
        ))}
      </div>

      <div style={styles.editorLayout}>
        {/* Canvas area */}
        <div style={styles.canvasArea}>
          <div style={styles.canvasWrapper}>
            <img
              ref={imgRef}
              src={currentUrl}
              alt="Asset"
              style={styles.assetImg}
            />
            {mode === 'mask' && (
              <canvas
                ref={maskCanvasRef}
                style={styles.maskCanvas}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
              />
            )}
          </div>

          {/* Mask controls */}
          {mode === 'mask' && (
            <div style={styles.maskControls}>
              <label style={styles.label}>Brush size: {brushSize}px</label>
              <input
                type="range"
                min={5}
                max={100}
                value={brushSize}
                onChange={e => setBrushSize(Number(e.target.value))}
                style={styles.slider}
              />
              <button style={styles.clearBtn} onClick={clearMask}>Clear mask</button>
            </div>
          )}
        </div>

        {/* Right panel */}
        <div style={styles.rightPanel}>
          {/* Layer panel */}
          {mode === 'layer' && (
            <div style={styles.layerPanel}>
              <div style={styles.panelTitle}>Layers</div>
              {layers.map(layer => (
                <div key={layer.name} style={styles.layerRow}>
                  <input
                    type="checkbox"
                    checked={layer.visible}
                    onChange={e => setLayers(prev =>
                      prev.map(l => l.name === layer.name ? { ...l, visible: e.target.checked } : l)
                    )}
                    style={styles.checkbox}
                  />
                  <div style={styles.layerThumb}>
                    {layer.url && (
                      <img src={layer.url} alt={layer.label} style={styles.thumbImg} />
                    )}
                  </div>
                  <span style={styles.layerLabel}>{layer.label}</span>
                </div>
              ))}
              <p style={styles.layerHint}>
                Toggle layers to preview. To edit text or logo, switch to Text Edit mode.
              </p>
            </div>
          )}

          {/* Text / Mask instruction */}
          {(mode === 'text' || mode === 'mask') && (
            <div style={styles.instructionPanel}>
              <div style={styles.panelTitle}>
                {mode === 'text' ? 'Describe your edit' : 'Describe what to replace in the painted area'}
              </div>
              <textarea
                style={styles.textarea}
                placeholder={
                  mode === 'text'
                    ? 'e.g. "make the background more dramatic, darker sky"'
                    : 'e.g. "replace with a larger, bolder version of the logo"'
                }
                value={instruction}
                onChange={e => setInstruction(e.target.value)}
                rows={3}
              />

              <label style={styles.checkboxRow}>
                <input
                  type="checkbox"
                  checked={applyToAll}
                  onChange={e => setApplyToAll(e.target.checked)}
                />
                <span style={styles.checkboxLabel}>Apply to all aspect ratios (1:1, 9:16, 16:9)</span>
              </label>

              {editError && (
                <div style={styles.errorBox}>{editError}</div>
              )}

              <button
                style={{
                  ...styles.editBtn,
                  opacity: isEditing || !instruction.trim() ? 0.5 : 1,
                  cursor: isEditing || !instruction.trim() ? 'not-allowed' : 'pointer',
                }}
                onClick={handleEdit}
                disabled={isEditing || !instruction.trim()}
              >
                {isEditing ? 'Editing... (~5–10s)' : 'Apply Edit →'}
              </button>
            </div>
          )}

          {/* Edit history */}
          {editHistory.length > 0 && (
            <div style={styles.historyPanel}>
              <div style={styles.panelTitle}>
                Edit History
                <button style={styles.undoBtn} onClick={handleUndo}>↩ Undo</button>
              </div>
              {editHistory.slice(0, 3).map((h, i) => (
                <div key={i} style={styles.historyRow}>
                  <img src={h.before} alt="before" style={styles.historyThumb} />
                  <span style={styles.historyArrow}>→</span>
                  <img src={h.after} alt="after" style={styles.historyThumb} />
                  <span style={styles.historyInstruction}>{h.instruction}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
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
  modeTabs: {
    display: 'flex',
    borderBottom: '1px solid #2a2a2a',
  },
  modeTab: {
    flex: 1,
    padding: '10px 16px',
    background: 'transparent',
    border: 'none',
    color: '#888',
    fontSize: 12,
    cursor: 'pointer',
    fontWeight: 500,
  },
  modeTabActive: {
    color: '#e8e8e8',
    background: '#222',
    borderBottom: '2px solid #1d4ed8',
  },
  editorLayout: {
    display: 'grid',
    gridTemplateColumns: '1fr 280px',
    gap: 0,
    minHeight: 400,
  },
  canvasArea: {
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    borderRight: '1px solid #2a2a2a',
  },
  canvasWrapper: {
    position: 'relative',
    display: 'inline-block',
  },
  assetImg: {
    width: '100%',
    maxHeight: 400,
    objectFit: 'contain',
    borderRadius: 8,
    display: 'block',
  },
  maskCanvas: {
    position: 'absolute',
    top: 0,
    left: 0,
    width: '100%',
    height: '100%',
    opacity: 0.5,
    cursor: 'crosshair',
    borderRadius: 8,
  },
  maskControls: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    flexWrap: 'wrap',
  },
  label: { fontSize: 11, color: '#888' },
  slider: { flex: 1, minWidth: 80 },
  clearBtn: {
    fontSize: 11,
    padding: '4px 10px',
    background: '#333',
    border: '1px solid #444',
    color: '#ccc',
    borderRadius: 6,
    cursor: 'pointer',
  },
  rightPanel: {
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    overflowY: 'auto',
  },
  panelTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: '#e8e8e8',
    marginBottom: 8,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  layerPanel: { display: 'flex', flexDirection: 'column', gap: 8 },
  layerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 8px',
    background: '#222',
    borderRadius: 6,
  },
  checkbox: { cursor: 'pointer' },
  layerThumb: {
    width: 32,
    height: 32,
    background: '#333',
    borderRadius: 4,
    overflow: 'hidden',
    flexShrink: 0,
  },
  thumbImg: { width: '100%', height: '100%', objectFit: 'cover' },
  layerLabel: { fontSize: 11, color: '#ccc' },
  layerHint: { fontSize: 10, color: '#555', marginTop: 4 },
  instructionPanel: { display: 'flex', flexDirection: 'column', gap: 10 },
  textarea: {
    background: '#111',
    border: '1px solid #333',
    color: '#e8e8e8',
    borderRadius: 8,
    padding: '10px 12px',
    fontSize: 12,
    resize: 'vertical',
    outline: 'none',
    fontFamily: 'inherit',
  },
  checkboxRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    cursor: 'pointer',
  },
  checkboxLabel: { fontSize: 11, color: '#888' },
  errorBox: {
    background: '#1f0a0a',
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    padding: '8px 10px',
    fontSize: 11,
    color: '#fca5a5',
  },
  editBtn: {
    width: '100%',
    padding: '10px 16px',
    background: 'linear-gradient(135deg, #1d4ed8, #7c3aed)',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  historyPanel: { display: 'flex', flexDirection: 'column', gap: 8 },
  undoBtn: {
    fontSize: 11,
    padding: '3px 8px',
    background: '#333',
    border: '1px solid #444',
    color: '#ccc',
    borderRadius: 6,
    cursor: 'pointer',
  },
  historyRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 8px',
    background: '#222',
    borderRadius: 6,
  },
  historyThumb: {
    width: 36,
    height: 36,
    objectFit: 'cover',
    borderRadius: 4,
  },
  historyArrow: { fontSize: 12, color: '#555' },
  historyInstruction: {
    fontSize: 10,
    color: '#666',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
}
