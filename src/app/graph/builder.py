"""Graph wiring. FROZEN node registry after the base graph block: user stories fill in node bodies
(agents/, guardrails/, graph/memory.py) but never add, remove or rewire nodes here.

START → input_guardrail ─blocked→ safe_response → END
                        └─ok→ summarize → supervisor ⇄ (knowledge | support)   [Send fan-out]
                                          supervisor → escalation → synthesize
                                          supervisor → synthesize | degraded_response
synthesize | degraded_response → output_guardrail → END
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agents.escalation import escalation_node
from app.agents.knowledge import knowledge_node
from app.agents.support import support_node
from app.graph.memory import summarize
from app.graph.state import AgentState, SpecialistInput
from app.graph.supervisor import supervisor
from app.graph.synthesize import degraded_response, safe_response, synthesize
from app.guardrails.input import input_guardrail, route_after_input
from app.guardrails.output import output_guardrail
from app.observability.tracing import traced_node


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    # Every node runs inside a span named after it (REQ-OBS-02): the trace of a turn shows the
    # nodes as children of http.request, with each node's LLM, tool and retrieval spans below.
    t = traced_node
    g = StateGraph(AgentState)
    g.add_node("input_guardrail", t("input_guardrail", input_guardrail))
    g.add_node("summarize", t("summarize", summarize))
    g.add_node(
        "supervisor",
        t("supervisor", supervisor),
        destinations=("knowledge", "support", "escalation", "synthesize", "degraded_response"),
    )
    g.add_node("knowledge", t("knowledge", knowledge_node), input_schema=SpecialistInput)
    g.add_node("support", t("support", support_node), input_schema=SpecialistInput)
    g.add_node("escalation", t("escalation", escalation_node))
    g.add_node("synthesize", t("synthesize", synthesize))
    g.add_node("degraded_response", t("degraded_response", degraded_response))
    g.add_node("safe_response", t("safe_response", safe_response))
    g.add_node("output_guardrail", t("output_guardrail", output_guardrail))

    g.add_edge(START, "input_guardrail")
    g.add_conditional_edges(
        "input_guardrail",
        route_after_input,
        {"safe_response": "safe_response", "summarize": "summarize"},
    )
    g.add_edge("summarize", "supervisor")
    g.add_edge("knowledge", "supervisor")
    g.add_edge("support", "supervisor")
    g.add_edge("escalation", "synthesize")
    g.add_edge("synthesize", "output_guardrail")
    g.add_edge("degraded_response", "output_guardrail")
    g.add_edge("output_guardrail", END)
    g.add_edge("safe_response", END)
    return g.compile(checkpointer=checkpointer)
