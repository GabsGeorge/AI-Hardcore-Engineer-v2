"""The planner sees every message the memory node has not folded yet (QA review N6)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.graph.memory import MEMORY_MAX_MESSAGES
from app.graph.supervisor import _planner_messages


def _thread(n: int) -> list:
    return [
        HumanMessage(content=f"pergunta {i}") if i % 2 == 0 else AIMessage(content=f"resposta {i}")
        for i in range(n)
    ]


@pytest.mark.parametrize("size", [7, 8])
def test_unfolded_thread_is_seen_whole(size: int) -> None:
    # Up to MEMORY_MAX_MESSAGES (8) the memory node does not fold, so nothing is in a summary:
    # the planner must get every message, including the first one. With the old window of 6,
    # threads of 7 and 8 messages hid their first messages from the planner.
    assert size <= MEMORY_MAX_MESSAGES
    thread = _thread(size)
    seen = [m for m in _planner_messages({"messages": thread}) if not isinstance(m, SystemMessage)]
    assert [m.content for m in seen] == [m.content for m in thread]


def test_folded_thread_gets_summary_and_recent_messages() -> None:
    thread = _thread(4)
    out = _planner_messages({"messages": thread, "summary": "lojista perguntou da taxa do Pix"})
    assert any(isinstance(m, SystemMessage) and "taxa do Pix" in m.content for m in out)
    assert [m.content for m in out if not isinstance(m, SystemMessage)] == [
        m.content for m in thread
    ]


def test_the_memory_node_does_not_fold_7_or_8_messages() -> None:
    """The other half of the guarantee: below the fold threshold nothing goes to the summary."""
    import asyncio

    from app.graph.memory import summarize

    for size in (7, 8):
        assert asyncio.run(summarize({"messages": _thread(size)})) == {}
