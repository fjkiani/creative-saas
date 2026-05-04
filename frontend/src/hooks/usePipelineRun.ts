import { useEffect, useState, useCallback } from 'react'
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
  id: string
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

function initNodeStates(): NodeState[] {
  return PIPELINE_NODES.map(n => ({ ...n, status: 'pending' }))
}

export function usePipelineRun(runId: string | null) {
  const [run, setRun] = useState<RunData | null>(null)
  const [nodes, setNodes] = useState<NodeState[]>(initNodeStates())
  const [assets, setAssets] = useState<Record<string, unknown>[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Apply a run_event to node states
  const applyEvent = useCallback((event: RunEvent) => {
    setNodes(prev => prev.map(n => {
      if (n.name !== event.node_name) return n
      const status: NodeStatus =
        event.status === 'STARTED'   ? 'running'   :
        event.status === 'COMPLETED' ? 'completed' :
        event.status === 'FAILED'    ? 'failed'    :
        event.status === 'SKIPPED'   ? 'skipped'   : n.status
      return { ...n, status, payload: event.payload, timestamp: event.created_at }
    }))
  }, [])

  // Fetch initial run data + events
  useEffect(() => {
    if (!runId) return
    setLoading(true)
    setNodes(initNodeStates())

    const fetchRun = async () => {
      try {
        const res = await fetch(`/api/runs/${runId}`)
        if (!res.ok) throw new Error(`Run not found: ${res.status}`)
        const data: RunData = await res.json()
        setRun(data)

        // Replay existing events to restore node states
        const eventsRes = await fetch(`/api/runs/${runId}/events`)
        const events: RunEvent[] = await eventsRes.json()
        events.forEach(applyEvent)

        // Load assets from run_report if complete
        if (data.run_report?.asset_summary) {
          const summary = data.run_report.asset_summary as Record<string, unknown>
          setAssets((summary.assets as Record<string, unknown>[]) || [])
        }
      } catch (e) {
        setError(String(e))
      } finally {
        setLoading(false)
      }
    }

    fetchRun()
  }, [runId, applyEvent])

  // Subscribe to Supabase Realtime for live node updates
  useEffect(() => {
    if (!runId) return

    const channel = supabase
      .channel(`run-${runId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'run_events',
          filter: `run_id=eq.${runId}`,
        },
        (payload) => {
          const event = payload.new as RunEvent
          applyEvent(event)
        }
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'runs',
          filter: `id=eq.${runId}`,
        },
        (payload) => {
          const updated = payload.new as RunData
          setRun(updated)
          if (updated.run_report?.asset_summary) {
            const summary = updated.run_report.asset_summary as Record<string, unknown>
            setAssets((summary.assets as Record<string, unknown>[]) || [])
          }
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [runId, applyEvent])

  return { run, nodes, assets, loading, error }
}
