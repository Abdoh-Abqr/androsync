#!/usr/bin/env python3
"""Build one standalone HTML page from docs/architecture/*.md, with every Mermaid diagram rendered.

The page needs no internet: the Mermaid library is embedded (about 3 MB), so do not commit the result.
Open it with `xdg-open docs/architecture/architecture.html`.

One-time setup (any folder outside the repo):
    mkdir -p /tmp/mmcheck && cd /tmp/mmcheck && npm i mermaid@10.9.3
Usage, from the repo root:
    python3 scripts/build_architecture_page.py --mermaid /tmp/mmcheck/node_modules/mermaid/dist/mermaid.min.js
Needs the Python package `markdown` (apt: python3-markdown).
"""
import argparse
import html
import pathlib
import re

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "architecture"
FILES = [("README.md", "index", "Context and containers"), ("components.md", "components", "Components"),
         ("data.md", "data", "Data"), ("behavior.md", "behavior", "Behavior"), ("overview.md", "overview", "Safety and decisions")]
LINKS = {name: f"#{sid}" for name, sid, _ in FILES}

CSS = """
:root { --bg:#f4f6f8; --surface:#ffffff; --fg:#1c2530; --muted:#566373; --line:#d6dee6; --accent:#0b6e8a; --code-bg:#e9eef3;
  --sans:"IBM Plex Sans","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#11161c; --surface:#171e26; --fg:#e3e9ef; --muted:#98a5b3; --line:#2a3541; --accent:#5cc3df; --code-bg:#1e2833; color-scheme: dark; } }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 var(--sans); }
.wrap { max-width:1040px; margin:0 auto; padding:8px 16px 64px; }
header.top { padding-block:28px 12px; } header.top h1 { font-size:1.9rem; line-height:1.2; margin:0 0 6px; } header.top p { margin:0; color:var(--muted); max-width:70ch; }
nav.bar { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:6px; padding-block:10px; background:var(--bg); border-bottom:1px solid var(--line); }
nav.bar a { color:var(--fg); text-decoration:none; padding:5px 12px; border:1px solid var(--line); border-radius:999px; font-size:13px; background:var(--surface); }
nav.bar a:hover { border-color:var(--accent); color:var(--accent); }
section { padding-top:28px; scroll-margin-top:64px; } section h1 { font-size:1.6rem; margin:0 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h2 { font-size:1.2rem; margin:34px 0 8px; } h3 { font-size:1rem; margin:24px 0 6px; color:var(--accent); } p, li { max-width:76ch; } a { color:var(--accent); }
code { font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:4px; }
pre:not(.mermaid) { background:var(--code-bg); padding:12px 14px; border-radius:6px; overflow-x:auto; } pre:not(.mermaid) code { background:none; padding:0; }
.diagram { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:16px; margin:14px 0; overflow-x:auto; }
.diagram pre.mermaid { margin:0; background:none; text-align:center; }
.tablewrap { overflow-x:auto; margin:12px 0; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
table { border-collapse:collapse; width:100%; font-size:13.5px; } th, td { text-align:left; vertical-align:top; padding:8px 12px; border-bottom:1px solid var(--line); min-width:90px; }
th { font-weight:600; background:var(--code-bg); } tr:last-child td { border-bottom:0; } footer { margin-top:48px; color:var(--muted); font-size:13px; }
"""


def render(text):
    blocks = []

    def grab(match):
        blocks.append(match.group(1))
        return f"\n\n@@MERMAID{len(blocks) - 1}@@\n\n"

    text = re.sub(r"```mermaid\n(.*?)```", grab, text, flags=re.S)
    text = re.sub(r"\]\(([A-Za-z]+\.md)\)", lambda m: f"]({LINKS.get(m.group(1), m.group(1))})", text)
    out = markdown.markdown(text, extensions=["tables", "fenced_code"])
    for i, block in enumerate(blocks):
        out = out.replace(f"<p>@@MERMAID{i}@@</p>", f'<div class="diagram"><pre class="mermaid">{html.escape(block.strip())}</pre></div>')
    return out.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>"), len(blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mermaid", required=True, help="path to mermaid.min.js")
    parser.add_argument("--out", default=str(ROOT / "architecture.html"))
    args = parser.parse_args()
    library = pathlib.Path(args.mermaid).read_text(encoding="utf-8").replace("</script", "<\\/script")

    sections, total = [], 0
    for fname, sid, _ in FILES:
        body, n = render((ROOT / fname).read_text(encoding="utf-8"))
        total += n
        sections.append(f'<section id="{sid}">{body}</section>')
    nav = "".join(f'<a href="#{sid}">{label}</a>' for _, sid, label in FILES)
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>AndroSync Architecture</title><style>{CSS}</style></head><body><div class=\"wrap\">"
            f'<header class="top"><h1>AndroSync Architecture</h1><p>How the laptop service, sync engine and Android app fit together. '
            f"{total} diagrams, each marked as built or target.</p></header>"
            f'<nav class="bar" aria-label="Sections">{nav}</nav>{"".join(sections)}'
            f"<footer>Generated from docs/architecture/*.md by scripts/build_architecture_page.py. Works offline.</footer></div>"
            f"<script>{library}</script>"
            "<script>mermaid.initialize({startOnLoad:true,securityLevel:'loose',"
            "theme:(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'default'});</script>"
            "</body></html>")
    pathlib.Path(args.out).write_text(page, encoding="utf-8")
    print(f"wrote {args.out}: {total} diagrams, {len(page) // 1024} KB")


main()
