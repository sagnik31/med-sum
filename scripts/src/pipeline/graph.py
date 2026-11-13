"""
The document pipeline as one LangGraph graph: the nodes of
pipeline/nodes.py joined by the routing of pipeline/routing.py.

    open -> transcribe (once per page) -> classify
    classify -> parse (once per part) -> validate -> render -> finish -> end
    classify -> validate                (type other: its pages only)
    parse, repair -> repair | fallback  (the reply was refused or cut off)
    validate -> repair | fallback       (error findings)
    fallback -> parse                   (the fallback model, from the start)

run_document() runs it for one document inside a pipeline run, and ends
the run with the end state the finish node set (verified or
needs_review). Anything that raises (an unreadable file, no model
answering, a record that doesn't trace to its steps) ends the run as
failed with the error, and is raised again for the caller.
"""

from langgraph.graph import END, START, StateGraph

from observability import start_run
from pipeline import routing
from pipeline.nodes import NODES
from pipeline.state import GraphState

# Stored on every run; change it when the graph's shape changes.
GRAPH_VERSION = "document-graph-4"

# Node visits a run may make: up to 20 pages, each model's round of parse
# parts (a page or less each), three attempts per model with their
# validations, and the fixed nodes, with room.
RECURSION_LIMIT = 300

_ROUTES = {
    "transcribe": (routing.after_transcribe, ["transcribe", "classify"]),
    "classify": (routing.after_classify, ["parse", "validate"]),
    "parse": (
        routing.after_parse,
        ["parse", "validate", "repair", "fallback", "render"],
    ),
    "repair": (routing.after_parse, ["validate", "repair", "fallback", "render"]),
    "validate": (routing.after_validate, ["render", "repair", "fallback", "validate"]),
}


def build(checkpointer=None, **compile_options):
    """
    The compiled graph; with a checkpointer, every node's state is saved.
    `compile_options` go to LangGraph's compile (e.g. interrupt_before).
    """
    builder = StateGraph(GraphState)
    for name, node in NODES.items():
        builder.add_node(name, node)
    builder.add_edge(START, "open")
    builder.add_edge("open", "transcribe")
    for name, (route, targets) in _ROUTES.items():
        builder.add_conditional_edges(name, route, targets)
    builder.add_edge("fallback", "parse")
    builder.add_edge("render", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer, **compile_options)


def run_document(
    state: GraphState,
    *,
    graph=None,
    graph_version: str = GRAPH_VERSION,
    connect=None,
) -> GraphState:
    """
    One document through the graph, as one pipeline run. Returns the final
    state; its end_state is the run's.
    """
    graph = graph or build()
    with start_run(state["document_id"], graph_version, connect=connect) as run:
        final = graph.invoke(
            state,
            config={
                "recursion_limit": RECURSION_LIMIT,
                "configurable": {"thread_id": run.id},
            },
        )
        run.finish(final["end_state"])
    return final
