import { useEffect, useState, useCallback, useRef } from 'react'

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
      // Only update if the new status is "later" in the lifecycle
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

export function usePipelineRun(runId: string | null) {
  const [run, setRun] = useState<RunData | null>(null)
  const [nodes, setNodes] = useState<NodeState[]>(initNodeStates())
  const [assets, setAssets] = useState<Record<string, unknown>[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Track seen event timestamps to avoid re-applying duplicates
  const seenEventsRef = useRef<Set<string>>(new Set())
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollingRef.current !== null) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
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

      // Extract assets from run_report
      if (runData.run_report?.asset_summary) {
        const summary = runData.run_report.asset_summary as Record<string, unknown>
        setAssets((summary.assets as Record<string, unknown>[]) || [])
      }

      // Stop polling once terminal
      if (TERMINAL_STATUSES.has(runData.status)) {
        stopPolling()
      }
    } catch (e) {
      setError(String(e))
    }
  }, [stopPolling])

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

    // Initial fetch
    fetchAndApply(runId).finally(() => setLoading(false))

    // Start polling
    pollingRef.current = setInterval(() => {
      fetchAndApply(runId)
    }, POLL_INTERVAL_MS)

    return () => {
      stopPolling()
    }
  }, [runId, fetchAndApply, stopPolling])

  return { run, nodes, assets, loading, error }
}
