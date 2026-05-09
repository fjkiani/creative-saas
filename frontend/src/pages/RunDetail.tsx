/**
 * RunDetail — CreativeOS v4
 *
 * v4 additions:
 *   - VideoPlayer component (below asset grid)
 *   - PublishPanel component (in sidebar)
 *   - CanvasEditor modal (click any asset to edit)
 *   - Pipeline tracker updated for 9 nodes
 */
import React, { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { usePipelineRun } from '../hooks/usePipelineRun'
import { PipelineTracker } from '../components/PipelineTracker'
import { AssetGrid } from '../components/AssetGrid'
import { CompliancePanel } from '../components/CompliancePanel'
import { ReviewCard } from '../components/ReviewCard'
import { VideoPlayer } from '../components/VideoPlayer'
import { PublishPanel } from '../components/PublishPanel'
import { CanvasEditor } from '../components/CanvasEditor'

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>()
  const { run, nodes, assets, loading, error, refetch } = usePipelineRun(runId ?? null)
  const [showReport, setShowReport] = useState(false)
  const [reviewDone, setReviewDone] = useState(false)
  const [editingAsset, setEditingAsset] = useState<{ id: string; url: string } | null>(null)
  const [hoveredAsset, setHoveredAsset] = useState<number | null>(null)

  if (loading) {
    return (
      <div style={styles.loading}>
        <div style={styles.loadingSpinner}>⟳</div>
        <div style={styles.loadingText}>Loading run...</div>
      </div>
    )
  }

  if (error || !run) {
    return (
      <div style={styles.errorPage}>
        <div style={styles.errorText}>{error || 'Run not found'}</div>
        <Link to="/" style={styles.backLink}>← New Campaign</Link>
      </div>
    )
  }

  const brief = run.brief as Record<string, unknown>
  const preCompliance = run.run_report?.compliance
    ? (run.run_report.compliance as Record<string, unknown>).pre_generation as Record<string, unknown>
    : null
  const postCompliance = run.run_report?.compliance
    ? (run.run_report.compliance as Record<string, unknown>).post_generation as Record<string, unknown>
    : null

  const isPendingReview = run.status === 'PENDING_REVIEW' && !reviewDone

  const sampleAssets = (
    (run.run_report?.assets as Array<Record<string, unknown>> | undefined) ?? []
  ).slice(0, 3).map((a) => ({
    storage_url: String(a.storage_url ?? ''),
    product_id: String(a.product_id ?? ''),
    market: String(a.market ?? ''),
    aspect_ratio: String(a.aspect_ratio ?? ''),
  }))

  const handleReviewed = (_decision: 'approve' | 'reject') => {
    setReviewDone(true)
    setTimeout(() => refetch?.(), 2000)
  }

  // Check if competitor analysis was used
  const hasCompetitorBrief = !!(run.run_report as Record<string, unknown> | undefined)?.competitor_brief

  return (
    <div style={styles.page}>
      <div style={styles.container}>
        {/* Header */}
        <div style={styles.header}>
          <Link to="/" style={styles.backLink}>← New Campaign</Link>
          <div style={styles.headerRight}>
            <div style={styles.runMeta}>
              <span style={styles.runId}>Run: {runId?.slice(0, 8)}...</span>
              <span style={styles.metaSep}>·</span>
              <span style={styles.metaItem}>LLM: {run.provider_llm}</span>
              <span style={styles.metaSep}>·</span>
              <span style={styles.metaItem}>Image: {run.provider_image}</span>
              {hasCompetitorBrief && (
                <>
                  <span style={styles.metaSep}>·</span>
                  <span style={styles.competitorTag}>⚔ Counter-brief active</span>
                </>
              )}
              <span style={styles.metaSep}>·</span>
              <span style={{
                ...styles.statusBadge,
                background: statusColor(run.status).bg,
                color: statusColor(run.status).text,
              }}>
                {run.status}
              </span>
            </div>
          </div>
        </div>

        {/* Campaign info */}
        <div style={styles.campaignBar}>
          <div style={styles.campaignInfo}>
            <span style={styles.campaignId}>{String(brief.campaign_id || 'Campaign')}</span>
            <span style={styles.campaignBrand}>Brand: {String(brief.brand || '')}</span>
            {Array.isArray(brief.products) && (
              <span style={styles.campaignMeta}>{brief.products.length} products</span>
            )}
            {Array.isArray(brief.markets) && (
              <span style={styles.campaignMeta}>{brief.markets.length} markets</span>
            )}
          </div>
          <div style={styles.campaignActions}>
            {run.status === 'COMPLETE' && assets.length > 0 && (
              <button style={styles.actionBtn} onClick={() => {
                alert('In production: downloads a ZIP of all assets organized by product/ratio')
              }}>
                Download All Assets
              </button>
            )}
          </div>
        </div>

        {/* HITL Review Card */}
        {isPendingReview && (
          <ReviewCard
            runId={runId ?? ''}
            reviewScore={run.review_score ?? null}
            preCompliance={preCompliance as ReviewCard['preCompliance']}
            sampleAssets={sampleAssets}
            onReviewed={handleReviewed}
            apiUrl={import.meta.env.VITE_API_URL}
            apiKey={import.meta.env.VITE_API_KEY}
          />
        )}

        {reviewDone && (
          <div style={styles.reviewDoneBanner}>
            ✓ Review decision submitted — pipeline resuming...
          </div>
        )}

        {/* Main layout */}
        <div style={styles.mainGrid}>
          {/* Left: Pipeline tracker + compliance + publish */}
          <div style={styles.sidebar}>
            <PipelineTracker nodes={nodes} runStatus={run.status} />
            <CompliancePanel
              preCompliance={preCompliance as Record<string, unknown> & { passed: boolean; issues: []; warnings: []; errors: [] } | null}
              postCompliance={postCompliance as Record<string, unknown> & { passed: boolean; issues: []; warnings: []; errors: [] } | null}
            />

            {/* v4: Publish panel */}
            <PublishPanel
              runId={runId ?? ''}
              runStatus={run.status}
              apiUrl={import.meta.env.VITE_API_URL}
              apiKey={import.meta.env.VITE_API_KEY}
            />

            {/* Run report */}
            {run.run_report && (
              <div style={styles.reportCard}>
                <button
                  style={styles.reportToggle}
                  onClick={() => setShowReport(!showReport)}
                >
                  {showReport ? '▲' : '▼'} run_report.json
                </button>
                {showReport && (
                  <pre style={styles.reportJson}>
                    {JSON.stringify(run.run_report, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>

          {/* Right: Asset gallery + video + canvas editor */}
          <div style={styles.main}>
            <div style={styles.sectionTitle}>
              Generated Creatives
              {assets.length > 0 && (
                <span style={styles.assetCount}>{assets.length} assets</span>
              )}
              {run.status === 'COMPLETE' && assets.length > 0 && (
                <span style={styles.editHint}>Click any asset to edit in canvas</span>
              )}
            </div>

            {/* Asset grid with click-to-edit */}
            <div style={styles.assetGridWrapper}>
              {(assets as Array<Record<string, unknown>>).map((asset, i) => (
                <div
                  key={i}
                  style={styles.assetItem}
                  onMouseEnter={() => setHoveredAsset(i)}
                  onMouseLeave={() => setHoveredAsset(null)}
                  onClick={() => {
                    if (run.status === 'COMPLETE' && asset.id) {
                      setEditingAsset({
                        id: String(asset.id),
                        url: String(asset.storage_url || ''),
                      })
                    }
                  }}
                >
                  <img
                    src={String(asset.storage_url || '')}
                    alt={`${asset.product_id} ${asset.market}`}
                    style={styles.assetImg}
                  />
                  <div style={styles.assetMeta}>
                    <span>{String(asset.product_id || '')}</span>
                    <span style={styles.assetRatio}>{String(asset.aspect_ratio || '')}</span>
                  </div>
                  {run.status === 'COMPLETE' && (
                    <div style={{
                      ...styles.editOverlay,
                      opacity: hoveredAsset === i ? 1 : 0,
                      transition: 'opacity 0.15s',
                    }}>
                      ✏️ Edit in Canvas
                    </div>
                  )}
                </div>
              ))}
              {assets.length === 0 && (
                <AssetGrid assets={assets as Parameters<typeof AssetGrid>[0]['assets']} />
              )}
            </div>

            {/* v4: Video player */}
            <VideoPlayer
              runId={runId ?? ''}
              runStatus={run.status}
              apiUrl={import.meta.env.VITE_API_URL}
              apiKey={import.meta.env.VITE_API_KEY}
            />
          </div>
        </div>

        {/* Canvas Editor Modal */}
        {editingAsset && (
          <div style={styles.modalOverlay} onClick={() => setEditingAsset(null)}>
            <div style={styles.modalContent} onClick={e => e.stopPropagation()}>
              <div style={styles.modalHeader}>
                <div style={styles.modalTitle}>Canvas Editor</div>
                <button style={styles.modalClose} onClick={() => setEditingAsset(null)}>×</button>
              </div>
              <CanvasEditor
                assetId={editingAsset.id}
                assetUrl={editingAsset.url}
                runId={runId ?? ''}
                apiUrl={import.meta.env.VITE_API_URL}
                apiKey={import.meta.env.VITE_API_KEY}
                onEditComplete={(newUrl) => {
                  setEditingAsset(prev => prev ? { ...prev, url: newUrl } : null)
                  refetch?.()
                }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function statusColor(status: string): { bg: string; text: string } {
  switch (status) {
    case 'COMPLETE':    return { bg: 'rgba(34,197,94,0.15)',  text: '#22c55e' }
    case 'RUNNING':     return { bg: 'rgba(59,130,246,0.15)', text: '#3b82f6' }
    case 'PENDING_REVIEW': return { bg: 'rgba(245,158,11,0.15)', text: '#f59e0b' }
    case 'REJECTED':    return { bg: 'rgba(239,68,68,0.15)',  text: '#ef4444' }
    case 'FAILED':      return { bg: 'rgba(239,68,68,0.15)',  text: '#ef4444' }
    default:            return { bg: 'rgba(100,100,100,0.15)', text: '#888' }
  }
}

const styles: Record<string, React.CSSProperties> = {
  page: { minHeight: '100vh', padding: '24px', background: '#0f0f0f' },
  container: { maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 },
  loading: {
    minHeight: '100vh', display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', gap: 12,
  },
  loadingSpinner: { fontSize: 32, color: '#1d4ed8' },
  loadingText: { color: '#666', fontSize: 14 },
  errorPage: {
    minHeight: '100vh', display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', gap: 16,
  },
  errorText: { color: '#fca5a5', fontSize: 14 },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  backLink: { fontSize: 13, color: '#888', textDecoration: 'none' },
  headerRight: {},
  runMeta: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' },
  runId: { fontSize: 12, color: '#555', fontFamily: 'monospace' },
  metaSep: { color: '#333' },
  metaItem: { fontSize: 12, color: '#666' },
  competitorTag: {
    fontSize: 10, fontWeight: 700, padding: '2px 8px',
    borderRadius: 20, background: 'rgba(124,58,237,0.15)', color: '#c4b5fd',
  },
  statusBadge: {
    fontSize: 10, fontWeight: 700, padding: '2px 8px',
    borderRadius: 20, letterSpacing: '0.04em',
  },
  campaignBar: {
    background: '#1a1a1a', border: '1px solid #2a2a2a',
    borderRadius: 10, padding: '14px 20px',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  },
  campaignInfo: { display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' },
  campaignId: { fontSize: 16, fontWeight: 700, color: '#e8e8e8' },
  campaignBrand: { fontSize: 12, color: '#888' },
  campaignMeta: {
    fontSize: 11, color: '#666', background: '#2a2a2a',
    padding: '2px 8px', borderRadius: 20,
  },
  campaignActions: {},
  actionBtn: {
    fontSize: 12, padding: '8px 16px', borderRadius: 8,
    background: '#1d4ed8', color: '#fff', border: 'none', cursor: 'pointer', fontWeight: 600,
  },
  reviewDoneBanner: {
    background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)',
    borderRadius: 8, padding: '12px 16px', fontSize: 13, color: '#22c55e', textAlign: 'center',
  },
  mainGrid: { display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20, alignItems: 'start' },
  sidebar: { display: 'flex', flexDirection: 'column', gap: 16 },
  main: { display: 'flex', flexDirection: 'column', gap: 16 },
  sectionTitle: {
    fontSize: 14, fontWeight: 600, color: '#e8e8e8',
    display: 'flex', alignItems: 'center', gap: 10,
  },
  assetCount: {
    fontSize: 11, color: '#888', background: '#2a2a2a', padding: '2px 8px', borderRadius: 20,
  },
  editHint: { fontSize: 11, color: '#555', marginLeft: 'auto' },
  assetGridWrapper: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: 12,
  },
  assetItem: {
    position: 'relative',
    background: '#1a1a1a',
    border: '1px solid #2a2a2a',
    borderRadius: 8,
    overflow: 'hidden',
    cursor: 'pointer',
  },
  assetImg: { width: '100%', display: 'block', aspectRatio: '1', objectFit: 'cover' },
  assetMeta: {
    padding: '6px 8px',
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 10,
    color: '#666',
  },
  assetRatio: { color: '#444' },
  // Note: hover effect on editOverlay requires a CSS class (inline styles can't do :hover).
  // Add to index.css: .asset-item:hover .edit-overlay { opacity: 1; }
  // The overlay is always visible at low opacity as a hint when run is COMPLETE.
  editOverlay: {
    position: 'absolute',
    inset: 0,
    background: 'rgba(0,0,0,0.45)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 13,
    color: '#fff',
    opacity: 0,
    // Opacity controlled via onMouseEnter/onMouseLeave on the parent div
  },
  reportCard: {
    background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: 10, overflow: 'hidden',
  },
  reportToggle: {
    width: '100%', padding: '12px 16px', background: 'transparent',
    border: 'none', color: '#888', fontSize: 12, cursor: 'pointer',
    textAlign: 'left', fontFamily: 'monospace',
  },
  reportJson: {
    padding: '0 16px 16px', fontSize: 10, color: '#666',
    fontFamily: 'monospace', overflowX: 'auto',
    maxHeight: 400, overflowY: 'auto',
    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  },
  // Modal
  modalOverlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
    padding: 24,
  },
  modalContent: {
    background: '#0f0f0f',
    border: '1px solid #2a2a2a',
    borderRadius: 16,
    width: '100%',
    maxWidth: 900,
    maxHeight: '90vh',
    overflow: 'auto',
  },
  modalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px 20px',
    borderBottom: '1px solid #2a2a2a',
  },
  modalTitle: { fontSize: 15, fontWeight: 600, color: '#e8e8e8' },
  modalClose: {
    background: 'transparent',
    border: 'none',
    color: '#888',
    fontSize: 20,
    cursor: 'pointer',
    lineHeight: 1,
  },
}
