#!/usr/bin/env python3
"""Save the GitHub Projects board as local files, so it can be read without signing in to GitHub.

Writes docs/planning/BOARD.md (renders on GitHub and in any editor) and docs/planning/board.html
(open it in a browser: `xdg-open docs/planning/board.html`). Both are snapshots; run this script again
to refresh them.

Needs the GitHub CLI logged in with the `project` scope:
    gh auth refresh -h github.com -s project -s read:project
Usage:
    python3 scripts/export_board.py [--owner OWNER] [--project NUMBER]
"""
import argparse
import datetime
import html
import json
import pathlib
import subprocess
import sys

COLUMNS = ["Ready", "In Progress", "In Review", "Backlog", "Done"]


def gh_json(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def load(owner, number):
    project = gh_json("project", "view", str(number), "--owner", owner, "--format", "json")
    items = gh_json("project", "item-list", str(number), "--owner", owner, "--format", "json", "--limit", "200")["items"]
    rows = []
    for item in items:
        content = item.get("content") or {}
        rows.append({
            "number": content.get("number"),
            "title": item.get("title", ""),
            "url": content.get("url", ""),
            "status": item.get("status") or "Backlog",
            "order": item.get("order"),
            "size": item.get("size") or "",
            "milestone": (item.get("milestone") or {}).get("title", ""),
            "labels": [x for x in item.get("labels", []) if x not in ("story", "epic")],
            "epic": "epic" in item.get("labels", []),
        })
    rows.sort(key=lambda r: (r["order"] is None, r["order"] or 0))
    return project, rows


def grouped(rows):
    groups = {name: [r for r in rows if r["status"] == name] for name in COLUMNS}
    for name in {r["status"] for r in rows} - set(COLUMNS):
        groups[name] = [r for r in rows if r["status"] == name]
    return groups


def write_markdown(path, project, rows, stamp):
    out = [f"# {project['title']} board", "",
           f"Snapshot of the GitHub Projects board, taken {stamp}. Live board: {project['url']} (private).",
           "Refresh with `python3 scripts/export_board.py`.", ""]
    for name, items in grouped(rows).items():
        if not items:
            continue
        out += [f"## {name} ({len(items)})", "", "| Order | Issue | Size | Milestone | Labels |", "|---|---|---|---|---|"]
        for r in items:
            tag = " (epic)" if r["epic"] else ""
            out.append(f"| {r['order'] if r['order'] is not None else ''} | [#{r['number']}]({r['url']}) {r['title']}{tag} "
                       f"| {r['size']} | {r['milestone']} | {', '.join(r['labels'])} |")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


CSS = """
:root { --bg:#f4f6f8; --card:#fff; --fg:#1c2530; --muted:#566373; --line:#d6dee6; --accent:#0b6e8a; }
@media (prefers-color-scheme: dark) { :root { --bg:#11161c; --card:#171e26; --fg:#e3e9ef; --muted:#98a5b3; --line:#2a3541; --accent:#5cc3df; } }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui, "Segoe UI", Roboto, sans-serif; }
header { padding:18px 20px 6px; } h1 { margin:0 0 4px; font-size:1.4rem; } header p { margin:0; color:var(--muted); }
.board { display:flex; gap:14px; padding:14px 20px 28px; overflow-x:auto; align-items:flex-start; }
.col { flex:0 0 300px; background:var(--bg); } .col h2 { font-size:.95rem; margin:0 0 8px; } .col h2 span { color:var(--muted); font-weight:400; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin-bottom:8px; }
.card a { color:var(--fg); text-decoration:none; font-weight:600; } .card a:hover { color:var(--accent); }
.meta { color:var(--muted); font-size:12px; margin-top:4px; } .pill { border:1px solid var(--line); border-radius:999px; padding:0 7px; margin-right:4px; }
"""


def write_html(path, project, rows, stamp):
    cols = []
    for name, items in grouped(rows).items():
        if not items:
            continue
        cards = []
        for r in items:
            pills = "".join(f'<span class="pill">{html.escape(x)}</span>' for x in [r["size"], r["milestone"]] if x)
            cards.append(f'<div class="card"><a href="{html.escape(r["url"])}">#{r["number"]} {html.escape(r["title"])}</a>'
                         f'<div class="meta">{pills}{"epic" if r["epic"] else ""}</div></div>')
        cols.append(f'<section class="col"><h2>{html.escape(name)} <span>{len(items)}</span></h2>{"".join(cards)}</section>')
    page = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(project["title"])} board</title><style>{CSS}</style>'
            f'<header><h1>{html.escape(project["title"])} board</h1><p>Snapshot taken {stamp}. Cards are in work order. '
            f'Live board: {html.escape(project["url"])}</p></header><div class="board">{"".join(cols)}</div>')
    path.write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default="Abdoh-Abqr")
    parser.add_argument("--project", type=int, default=1)
    args = parser.parse_args()
    project, rows = load(args.owner, args.project)
    stamp = datetime.date.today().isoformat()
    out_dir = pathlib.Path(__file__).resolve().parent.parent / "docs" / "planning"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_markdown(out_dir / "BOARD.md", project, rows, stamp)
    write_html(out_dir / "board.html", project, rows, stamp)
    print(f"wrote {out_dir / 'BOARD.md'} and {out_dir / 'board.html'} ({len(rows)} items)")


main()
