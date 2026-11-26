#!/usr/bin/env python3
"""Print one of our Markdown documents to PDF with headless Chrome (development only).

    python3 tools/md_to_pdf.py docs/PIPELINE_WALKTHROUGH.md docs/PIPELINE_WALKTHROUGH.pdf

Handles the Markdown our documents use: headings, paragraphs, lists, block quotes, fenced
code, tables, inline code, bold and links. Set CHROME to the browser binary if it is not in
the usual macOS place.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
STYLE = """
@page { size: A4; margin: 14mm 14mm; }
body { font: 10pt/1.4 "Helvetica Neue", Arial, sans-serif; color: #16202a; }
h1 { font-size: 17pt; margin: 0 0 6pt; }
h2 { font-size: 12.5pt; margin: 14pt 0 4pt; border-bottom: 0.8pt solid #9aa8b5; }
p, li { margin: 0 0 5pt; }
blockquote { margin: 4pt 0; padding: 2pt 8pt; border-left: 3pt solid #9aa8b5; color: #43525f; }
pre { background: #f3f6f8; border: 0.6pt solid #d4dbe1; padding: 5pt 7pt; font-size: 8.4pt;
      line-height: 1.3; white-space: pre-wrap; overflow-wrap: anywhere; break-inside: avoid; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.8pt; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0 7pt; font-size: 9pt;
        break-inside: auto; }
th, td { border: 0.6pt solid #c2ccd5; padding: 1.5pt 4pt; text-align: left; vertical-align: top; }
th { background: #eef2f5; }
tr { break-inside: avoid; }
"""


def inline(text: str) -> str:
    codes: list[str] = []

    def hold(m: re.Match[str]) -> str:
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = html.escape(re.sub(r"`([^`]*)`", hold, text), quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _cells(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def convert(markdown: str) -> str:
    lines, out, index = markdown.splitlines(), [], 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            flush()
            block = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
        elif re.match(r"^#{1,6} ", line):
            flush()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{inline(line[level + 1:])}</h{level}>")
        elif line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|[-|: ]+\|$",
                                                                         lines[index + 1]):
            flush()
            head = "".join(f"<th>{inline(c)}</th>" for c in _cells(line))
            rows = []
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                rows.append("<tr>" + "".join(f"<td>{inline(c)}</td>"
                                             for c in _cells(lines[index])) + "</tr>")
                index += 1
            out.append(f"<table><tr>{head}</tr>{''.join(rows)}</table>")
            continue
        elif re.match(r"^\s*[-*] ", line):
            flush()
            items = []
            while index < len(lines) and re.match(r"^\s*[-*] ", lines[index]):
                item = [lines[index].split(" ", 1)[1].strip()]
                index += 1
                while index < len(lines) and lines[index].startswith("  ") \
                        and lines[index].strip():
                    item.append(lines[index].strip())
                    index += 1
                items.append(f"<li>{inline(' '.join(item))}</li>")
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        elif line.startswith(">"):
            flush()
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: md_to_pdf.py <in.md> <out.pdf>", file=sys.stderr)
        return 2
    source, target = Path(argv[0]), Path(argv[1]).resolve()
    title = html.escape(source.stem.replace("_", " ").title())
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}'
            f"</title><style>{STYLE}</style></head><body>"
            f"{convert(source.read_text(encoding='utf-8'))}</body></html>")
    with tempfile.TemporaryDirectory() as folder:
        page_path = Path(folder) / "page.html"
        page_path.write_text(page, encoding="utf-8")
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={target}", page_path.as_uri()],
                       check=True, capture_output=True)
    print(f"wrote {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
