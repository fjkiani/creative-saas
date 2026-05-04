import React from 'react'
import { NodeState } from '../hooks/usePipelineRun'

interface Props {
  nodes: NodeState[]
  runStatus?: string
}

const STATUS_STYLES: Record<string, { dot: string; label: string; bar: string }> = {
  pending:   { dot: '#444',    label: '#666',    bar: '#222' },
  running:   { dot: '#f59e0b', label: '#f59e0b', bar: '#78350f' },
  completed: { dot: '#22c55e', label: '#86efac', bar: '#14532d' },
  failed:    { dot: '#ef4444', label: '#fca5a5', bar: '#7f1d1d' },
  skipped:   { dot: '#6b7280', label: '#9ca3af', bar: '#1f2937' },
}

const STATUS_ICONS: Record<string, string> = {
  pending:   '○',
  running:   '◉',
  completed: '✓',
  failed:    '✗',
  skipped:   '—',
}

export function PipelineTracker({ nodes, runStatus }: Props) {
  const completedCount = nodes.filter(n => n.status === 'completed').length
  const progress = Math.round((completedCount / nodes.length) * 100)

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>Pipeline Progress</span>
        <span style={{
          ...styles.badge,
          background: runStatus === 'COMPLETE' ? '#14532d' :
                      runStatus === 'FAILED'   ? '#7f1d1d' :
                      runStatus === 'RUNNING'  ? '#78350f' : '#1f2937',
          color: runStatus === 'COMPLETE' ? '#86efac' :
                 runStatus === 'FAILED'   ? '#fca5a5' :
                 runStatus === 'RUNNING'  ? '#fde68a' : '#9ca3af',
        }}>
          {runStatus || 'PENDING'}
        </span>
      </div>

      {/* Progress bar */}
      <div style={styles.progressTrack}>
        <div style={{ ...styles.progressFill, width: `${progress}%` }} />
      </div>
      <div style={styles.progressLabel}>{completedCount} / {nodes.length} nodes complete</div>

      {/* Node list */}
      <div style={styles.nodeList}>
        {nodes.map((node, i) => {
          const s = STATUS_STYLES[node.status] || STATUS_STYLES.pending
          return (
            <div key={node.name} style={styles.nodeRow}>
              {/* Connector line */}
              {i > 0 && (
                <div style={{
                  ...styles.connector,
                  background: nodes[i - 1].status === 'completed' ? '#22c55e' : '#333',
                }} />
              )}

              <div style={styles.nodeContent}>
                {/* Status dot */}
                <div style={{
                  ...styles.dot,
                  background: s.dot,
                  boxShadow: node.status === 'running' ? `0 0 8px ${s.dot}` : 'none',
                }}>
                  <span style={{ fontSize: 10, color: '#fff', fontWeight: 700 }}>
                    {STATUS_ICONS[node.status]}
                  </span>
                </div>

                {/* Node info */}
                <div style={styles.nodeInfo}>
                  <div style={{ ...styles.nodeLabel, color: s.label }}>
                    {node.label}
                    {node.status === 'running' && (
                      <span style={styles.spinner}> ⟳</span>
                    )}
                  </div>
                  {node.payload?.message && (
                    <div style={styles.nodeMessage}>
                      {String(node.payload.message)}
                    </div>
                  )}
                  {node.status === 'completed' && node.payload && (
                    <div style={styles.nodePayload}>
                      {Object.entries(node.payload)
                        .filter(([k]) => k !== 'message' && k !== 'issues')
                        .slice(0, 3)
                        .map(([k, v]) => (
                          <span key={k} style={styles.payloadChip}>
                            {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                          </span>
                        ))}
                    </div>
                  )}
                </div>

                {/* Timestamp */}
                {node.timestamp && (
                  <div style={styles.timestamp}>
                    {new Date(node.timestamp).toLocaleTimeString()}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: '#1a1a1a',
    border: '1px solid #2a2a2a',
    borderRadius: 12,
    padding: 24,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  title: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8e8e8',
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
  },
  badge: {
    fontSize: 11,
    fontWeight: 700,
    padding: '3px 10px',
    borderRadius: 20,
    letterSpacing: '0.08em',
  },
  progressTrack: {
    height: 4,
    background: '#2a2a2a',
    borderRadius: 2,
    marginBottom: 6,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #22c55e, #86efac)',
    borderRadius: 2,
    transition: 'width 0.4s ease',
  },
  progressLabel: {
    fontSize: 11,
    color: '#666',
    marginBottom: 20,
  },
  nodeList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
  },
  nodeRow: {
    position: 'relative',
  },
  connector: {
    position: 'absolute',
    left: 15,
    top: -8,
    width: 2,
    height: 8,
    borderRadius: 1,
  },
  nodeContent: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
    padding: '8px 0',
  },
  dot: {
    width: 32,
    height: 32,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'all 0.3s ease',
  },
  nodeInfo: {
    flex: 1,
    minWidth: 0,
  },
  nodeLabel: {
    fontSize: 13,
    fontWeight: 500,
    marginBottom: 2,
  },
  nodeMessage: {
    fontSize: 11,
    color: '#666',
    marginBottom: 4,
  },
  nodePayload: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
  },
  payloadChip: {
    fontSize: 10,
    background: '#2a2a2a',
    color: '#888',
    padding: '2px 6px',
    borderRadius: 4,
    fontFamily: 'monospace',
  },
  timestamp: {
    fontSize: 10,
    color: '#555',
    flexShrink: 0,
    paddingTop: 2,
  },
  spinner: {
    display: 'inline-block',
    animation: 'spin 1s linear infinite',
    color: '#f59e0b',
  },
}
