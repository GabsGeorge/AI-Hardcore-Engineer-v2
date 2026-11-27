"""Print the LangGraph graph as Mermaid, for the README block between the `graph:begin/end`
markers (`tests/unit/test_readme_graph.py` fails when the two drift apart).

usage: python scripts/graph_mermaid.py
"""

from app.graph.builder import build_graph


def mermaid() -> str:
    return str(build_graph(None).get_graph().draw_mermaid()).strip()  # type: ignore[arg-type]


if __name__ == "__main__":
    print(mermaid())
