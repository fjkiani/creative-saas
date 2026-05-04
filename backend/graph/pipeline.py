"""
LangGraph pipeline definition — v3.

Topology:
  START → enrich → prompt_gen → compliance_pre ──(pass)──→ image_gen → composite
                                               └─(fail)──→ END

  composite → review_gate ──(approved)──→ localize → compliance_post → END
                           └─(rejected)──→ END

Each node:
  - Reads from PipelineState
  - Writes its outputs back to PipelineState
  - Broadcasts a run_event to Supabase (drives Realtime UI)

Checkpointing:
  MemorySaver is used for in-process checkpointing — sufficient for a single
  Render instance. For multi-instance deployments, swap to AsyncPostgresSaver:

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    checkpointer = AsyncPostgresSaver.from_conn_string(DATABASE_URL)

  The review_gate node uses interrupt() to pause the graph when human review
  is required. The graph resumes via:

    await pipeline.ainvoke(
        Command(resume={"decision": "approve", "reviewer_notes": "..."}),
        config={"configurable": {"thread_id": run_id}},
    )

Background execution: FastAPI runs ainvoke() in a background asyncio task,
so the HTTP response returns immediately with the run_id.
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.graph.state import PipelineState
from backend.graph.nodes.enrich import enrich_node
from backend.graph.nodes.prompt_gen import prompt_gen_node
from backend.graph.nodes.compliance_pre import compliance_pre_node, compliance_pre_router
from backend.graph.nodes.image_gen import image_gen_node
from backend.graph.nodes.composite import composite_node
from backend.graph.nodes.review_gate import review_gate_node, review_gate_router
from backend.graph.nodes.localize import localize_node
from backend.graph.nodes.compliance_post import compliance_post_node

# Singleton MemorySaver — shared across all runs in this process.
# Each run uses its run_id as the thread_id for isolation.
checkpointer = MemorySaver()


def build_pipeline() -> StateGraph:
    """
    Build and compile the creative automation LangGraph pipeline.

    Returns a compiled graph ready for ainvoke().
    """
    graph = StateGraph(PipelineState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("enrich", enrich_node)
    graph.add_node("prompt_gen", prompt_gen_node)
    graph.add_node("compliance_pre", compliance_pre_node)
    graph.add_node("image_gen", image_gen_node)
    graph.add_node("composite", composite_node)
    graph.add_node("review_gate", review_gate_node)
    graph.add_node("localize", localize_node)
    graph.add_node("compliance_post", compliance_post_node)

    # ── Define edges ──────────────────────────────────────────────────────────
    graph.set_entry_point("enrich")
    graph.add_edge("enrich", "prompt_gen")
    graph.add_edge("prompt_gen", "compliance_pre")

    # Conditional edge: compliance_pre can halt the pipeline on hard errors
    graph.add_conditional_edges(
        "compliance_pre",
        compliance_pre_router,
        {
            "image_gen": "image_gen",
            "end_with_error": END,
        },
    )

    graph.add_edge("image_gen", "composite")

    # Conditional edge: review_gate routes to localize or END based on decision
    graph.add_edge("composite", "review_gate")
    graph.add_conditional_edges(
        "review_gate",
        review_gate_router,
        {
            "localize": "localize",
            "end_rejected": END,
        },
    )

    graph.add_edge("localize", "compliance_post")
    graph.add_edge("compliance_post", END)

    # Compile with MemorySaver for interrupt/resume support
    return graph.compile(checkpointer=checkpointer)


# Singleton compiled graph — built once at module import
pipeline = build_pipeline()
