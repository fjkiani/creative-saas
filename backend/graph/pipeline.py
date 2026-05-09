"""
LangGraph pipeline definition — CreativeOS v4.

Topology:
  START → competitor_analyze (optional) → enrich → prompt_gen → compliance_pre
        └─(no competitor data)──────────────────────────────────────────────────┘

  compliance_pre ──(pass)──→ image_gen → composite
                └─(fail)──→ END

  composite → review_gate ──(approved)──→ localize → compliance_post
                            └─(rejected)──→ END

  compliance_post → video_gen → publish → END

New nodes in v4:
  - competitor_analyze: optional entry point, injects style_hints into brief
  - video_gen: generates slideshow or AI video trailers
  - publish: publishes to Instagram and TikTok

Each node:
  - Reads from PipelineState
  - Writes its outputs back to PipelineState
  - Broadcasts a run_event to Supabase (drives Realtime UI)

Checkpointing:
  MemorySaver is used for in-process checkpointing — sufficient for a single
  Render instance. For multi-instance deployments, swap to AsyncPostgresSaver.
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.graph.state import PipelineState
from backend.graph.nodes.competitor_analyze import competitor_analyze_node
from backend.graph.nodes.enrich import enrich_node
from backend.graph.nodes.prompt_gen import prompt_gen_node
from backend.graph.nodes.compliance_pre import compliance_pre_node, compliance_pre_router
from backend.graph.nodes.image_gen import image_gen_node
from backend.graph.nodes.composite import composite_node
from backend.graph.nodes.review_gate import review_gate_node, review_gate_router
from backend.graph.nodes.localize import localize_node
from backend.graph.nodes.compliance_post import compliance_post_node
from backend.graph.nodes.video_gen import video_gen_node
from backend.graph.nodes.publish_node import publish_node

# Singleton MemorySaver — shared across all runs in this process.
checkpointer = MemorySaver()


def build_pipeline() -> StateGraph:
    """
    Build and compile the CreativeOS v4 LangGraph pipeline.
    Returns a compiled graph ready for ainvoke().
    """
    graph = StateGraph(PipelineState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("competitor_analyze", competitor_analyze_node)
    graph.add_node("enrich", enrich_node)
    graph.add_node("prompt_gen", prompt_gen_node)
    graph.add_node("compliance_pre", compliance_pre_node)
    graph.add_node("image_gen", image_gen_node)
    graph.add_node("composite", composite_node)
    graph.add_node("review_gate", review_gate_node)
    graph.add_node("localize", localize_node)
    graph.add_node("compliance_post", compliance_post_node)
    graph.add_node("video_gen", video_gen_node)
    graph.add_node("publish", publish_node)

    # ── Define edges ──────────────────────────────────────────────────────────
    # competitor_analyze is always the entry point (no-op if no competitor data)
    graph.set_entry_point("competitor_analyze")
    graph.add_edge("competitor_analyze", "enrich")
    graph.add_edge("enrich", "prompt_gen")
    graph.add_edge("prompt_gen", "compliance_pre")

    # Conditional: compliance_pre can halt on hard errors
    graph.add_conditional_edges(
        "compliance_pre",
        compliance_pre_router,
        {
            "image_gen": "image_gen",
            "end_with_error": END,
        },
    )

    graph.add_edge("image_gen", "composite")

    # Conditional: review_gate routes to localize or END
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
    graph.add_edge("compliance_post", "video_gen")
    graph.add_edge("video_gen", "publish")
    graph.add_edge("publish", END)

    # Compile with MemorySaver for interrupt/resume support
    return graph.compile(checkpointer=checkpointer)


# Singleton compiled graph — built once at module import
pipeline = build_pipeline()
