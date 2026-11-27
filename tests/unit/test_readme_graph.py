"""The Mermaid diagram in the README is the compiled graph, not a drawing (QA review N14)."""

import re
from pathlib import Path

import pytest

from app.graph.builder import build_graph

pytestmark = pytest.mark.req("REQ-ORC-01")

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_diagram_matches_the_compiled_graph() -> None:
    match = re.search(
        r"<!-- graph:begin -->\s*```mermaid\n(.*?)\n```\s*<!-- graph:end -->",
        README.read_text(encoding="utf-8"),
        re.S,
    )
    assert match, "README must keep the generated Mermaid block between the graph markers"
    expected = str(build_graph(None).get_graph().draw_mermaid()).strip()  # type: ignore[arg-type]
    assert match.group(1).strip() == expected, "run: python scripts/graph_mermaid.py"
