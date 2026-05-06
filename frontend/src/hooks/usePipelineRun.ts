import { useEffect, useState, useCallback, useRef } from 'react'
import { supabase } from '../lib/supabase'

export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface NodeState {
  name: string
  label: string
  status: NodeStatus
  payload?: Record<string, unknown>
  timestamp?: string
}

export interface RunEvent {
  id?: string
  run_id: string
  node_name: string
  status: string
  payload: Record<string, unknown>
  created_at: string
}

export interface RunData {
  id: string
  status: string
  provider_image: string
  provider_llm: string
  brief: Record<string, unknown>
  run_report: Record<string, unknown> | null
  error_message: string | null
  created_at: string
  completed_at: string | null
  review_score?: number | null
}

export interface AssetRow {
  id: string
  run_id: string
  product_id: string
  market: string
  aspect_ratio: string
  language: string
  storage_url: string
  storage_path: string
  reused: boolean
  compliance_passed: boolean | null
}

// Ordered pipeline nodes with display labels
const PIPELINE_NODES: { name: string; label: string }[] = [
  { name: 'enrich',           label: 'Brief Enrichment' },
  { name: 'prompt_gen',       label: 'Prompt Generation' },
  { name: 'compliance_pre',   label: 'Pre-flight Compliance' },
  { name: 'image_gen',        label: 'Image Generation' },
  { name: 'composite',        label: 'Compositing' },
  { name: 'localize',         label: 'Localization' },
  { name: 'compliance_post',  label: 'Post-generation Compliance' },
]

const TERMINAL_STATUSES = new Set(['COMPLETE', 'FAILED', 'REJECTED', 'PENDING_REVIEW'])
const POLL_INTERVAL_MS = 2500

function initNodeStates(): NodeState[] {
  return PIPELINE_NODES.map(n => ({ ...n, status: 'pending' }))
}

function applyEventsToNodes(
  nodes: NodeState[],
  events: RunEvent[]
): NodeState[] {
  let updated = [...nodes]
  for (const event of events) {
    updated = updated.map(n => {
      if (n.name !== event.node_name) return n
      const status: NodeStatus =
        event.status === 'STARTED'   ? 'running'   :
        event.status === 'COMPLETED' ? 'completed' :
        event.status === 'FAILED'    ? 'failed'    :
        event.status === 'SKIPPED'   ? 'skipped'   : n.status
      const order: Record<NodeStatus, number> = {
        pending: 0, running: 1, completed: 2, failed: 2, skipped: 2
      }
      if (order[status] >= order[n.status]) {
        return { ...n, status, payload: event.payload, timestamp: event.created_at }
      }
      return n
    })
  }
  return updated
}

/**
 * Merge a new asset row into the existing asset list.
 * Deduplicates by id; updates in place if already present.
 */
function mergeAsset(prev: AssetRow[], incoming: AssetRow): AssetRow[] {
  const idx = prev.findIndex(a => a.id === incoming.id)
  if (idx === -1) return [...prev, incoming]
  const next = [...prev]
  next[idx] = incoming
  return next
}

export function usePipelineRun(runId: string | null) {
  const [run, setRun] = useState<RunData | null>(null)
  const [nodes, setNodes] = useState<NodeState[]>(initNodeStates())
  // assets: live rows from Supabase `assets` table (primary source)
  // falls back to run_report.asset_summary when Supabase Realtime is unavailable
  const [assets, setAssets] = useState<AssetRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const seenEventsRef = useRef<Set<string>>(new Set())
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Track Supabase Realtime channel so we can unsubscribe on cleanup
  const realtimeChannelRef = useRef<ReturnType<typeof supabase.channel> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollingRef.current !== null) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [])

  const stopRealtime = useCallback(() => {
    if (realtimeChannelRef.current) {
      supabase.removeChannel(realtimeChannelRef.current)
      realtimeChannelRef.current = null
    }
  }, [])

  // ── Fetch assets from Supabase `assets` table ─────────────────────────────
  const fetchAssets = useCallback(async (id: string) => {
    try {
      const { data, error: err } = await supabase
        .from('assets')
        .select('*')
        .eq('run_id', id)
        .order('created_at')
      if (!err && data) {
        setAssets(data as AssetRow[])
      }
    } catch {
      // Non-fatal — fall back to run_report extraction below
    }
  }, [])

  const fetchAndApply = useCallback(async (id: string) => {
    try {
      // Fetch run status
      const runRes = await fetch(`/api/runs/${id}`)
      if (!runRes.ok) throw new Error(`Run fetch failed: ${runRes.status}`)
      const runData: RunData = await runRes.json()
      setRun(runData)

      // Fetch events and apply new ones
      const eventsRes = await fetch(`/api/runs/${id}/events`)
      if (eventsRes.ok) {
        const events: RunEvent[] = await eventsRes.json()
        const newEvents = events.filter(e => {
          const key = `${e.node_name}:${e.status}:${e.created_at}`
          if (seenEventsRef.current.has(key)) return false
          seenEventsRef.current.add(key)
          return true
        })
        if (newEvents.length > 0) {
          setNodes(prev => applyEventsToNodes(prev, newEvents))
        }
      }

      // Primary: pull from Supabase assets table
      await fetchAssets(id)

      // Fallback: if assets table is empty, extract from run_report
      setAssets(prev => {
        if (prev.length > 0) return prev  // already have live rows
        if (runData.run_report?.asset_summary) {
          const summary = runData.run_report.asset_summary as Record<string, unknown>
          const reportAssets = (summary.assets as AssetRow[]) || []
          return reportAssets
        }
        return prev
      })

      // Stop polling once terminal
      if (TERMINAL_STATUSES.has(runData.status)) {
        stopPolling()
      }
    } catch (e) {
      setError(String(e))
    }
  }, [stopPolling, fetchAssets])

  // ── Subscribe to Supabase Realtime for live asset inserts ─────────────────
  const startRealtime = useCallback((id: string) => {
    stopRealtime()

    const channel = supabase
      .channel(`assets:run_id=eq.${id}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'assets',
          filter: `run_id=eq.${id}`,
        },
        (payload) => {
          // New asset row arrived — merge into state immediately
          setAssets(prev => mergeAsset(prev, payload.new as AssetRow))
        }
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'assets',
          filter: `run_id=eq.${id}`,
        },
        (payload) => {
          setAssets(prev => mergeAsset(prev, payload.new as AssetRow))
        }
      )
      .subscribe()

    realtimeChannelRef.current = channel
  }, [stopRealtime])

  useEffect(() => {
    if (!runId) return

    // Reset state for new run
    setLoading(true)
    setRun(null)
    setNodes(initNodeStates())
    setAssets([])
    setError(null)
    seenEventsRef.current = new Set()
    stopPolling()
    stopRealtime()

    // Subscribe to live asset inserts via Supabase Realtime
    startRealtime(runId)

    // Initial fetch
    fetchAndApply(runId).finally(() => setLoading(false))

    // Polling fallback (catches run status + events; Realtime handles assets)
    pollingRef.current = setInterval(() => {
      fetchAndApply(runId)
    }, POLL_INTERVAL_MS)

    return () => {
      stopPolling()
      stopRealtime()
    }
  }, [runId, fetchAndApply, stopPolling, stopRealtime, startRealtime])

  // Expose refetch for manual refresh (used by ReviewCard after decision)
  const refetch = useCallback(() => {
    if (runId) fetchAndApply(runId)
  }, [runId, fetchAndApply])

  return { run, nodes, assets, loading, error, refetch }
}
