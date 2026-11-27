"""Export each page of docs/architecture.drawio to docs/architecture/<n>-<name>.png.

Renders with the official draw.io viewer (viewer.diagrams.net) in the Chromium of the Playwright
image already used by the E2E, so nothing is installed locally:

    python scripts/export_drawio.py

Needs Docker (or Podman: CONTAINER_CLI=podman) and network access to viewer.diagrams.net.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "architecture.drawio"
OUT = ROOT / "docs" / "architecture"
IMAGE = "mcr.microsoft.com/playwright:v1.49.0-jammy"
NAMES = {"arquitetura": "1-arquitetura", "grafo": "2-grafo", "rag": "3-pipeline-rag"}
PAGE = re.compile(
    r'<diagram id="([^"]+)"[^>]*>\s*<mxGraphModel[^>]*pageWidth="(\d+)" pageHeight="(\d+)"'
)


def main() -> None:
    xml = SOURCE.read_text(encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        jobs = []
        for index, match in enumerate(PAGE.finditer(xml)):
            page_id, width, height = match.group(1), int(match.group(2)), int(match.group(3))
            config = {"nav": False, "resize": False, "toolbar": "", "lightbox": False}
            config |= {"page": index, "border": 10, "xml": xml}
            (work / f"page{index}.html").write_text(
                '<!doctype html><html><head><meta charset="utf-8">'
                "<style>html,body{margin:0;background:#fff}</style></head><body>"
                f'<div class="mxgraph" data-mxgraph="{html.escape(json.dumps(config))}"></div>'
                '<script src="https://viewer.diagrams.net/js/viewer-static.min.js"></script>'
                "</body></html>",
                encoding="utf-8",
            )
            name = NAMES.get(page_id, page_id)
            jobs.append(
                f"$C --headless=new --no-sandbox --disable-gpu --hide-scrollbars "
                f"--force-device-scale-factor=1 --virtual-time-budget=40000 "
                f"--window-size={width + 80},{height + 200} "
                f"--screenshot=/work/{name}.png file:///work/page{index}.html </dev/null"
            )
        script = "C=$(ls -d /ms-playwright/chromium-*/chrome-linux/chrome | head -1); " + "; ".join(
            jobs
        )
        subprocess.run(
            [
                os.environ.get("CONTAINER_CLI", "docker"),
                "run",
                "--rm",
                "-v",
                f"{work}:/work",
                IMAGE,
                "bash",
                "-c",
                script,
            ],
            check=True,
        )
        for png in work.glob("*.png"):
            (OUT / png.name).write_bytes(png.read_bytes())
            print(f"docs/architecture/{png.name}")


if __name__ == "__main__":
    main()
