"""Builds report.html and slides.html from the templates in docs/, with every image embedded.

The templates use {{IMG:path}} (path relative to docs/) where an image goes. The output files are fully
self-contained, so they open by double-clicking on any computer.

Run: python scripts/build_docs.py
"""

import base64
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def embed(match: re.Match) -> str:
    path = DOCS / match.group(1)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


report = (DOCS / "report_template.html").read_text()
diagram = report[report.index("<svg"):report.index("</svg>") + len("</svg>")]  # one agent diagram, used in both

for name in ("report", "slides"):
    template = (DOCS / f"{name}_template.html").read_text().replace("{{SVG}}", diagram)
    html = re.sub(r"\{\{IMG:([^}]+)\}\}", embed, template)
    (ROOT / f"{name}.html").write_text(html)
    print(f"wrote {name}.html ({len(html) // 1024} KB)")
