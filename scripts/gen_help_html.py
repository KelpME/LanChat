#!/usr/bin/env python3
"""Generate HELP.html from HELP.md with clean, self-contained styling."""
import pathlib

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
src = ROOT / "HELP.md"
out = ROOT / "HELP.html"

md = markdown.Markdown(extensions=["tables", "fenced_code"])
body = md.convert(src.read_text(encoding="utf-8"))

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lanchat Help</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    max-width: 760px; margin: 2rem auto; padding: 0 1.25rem;
    line-height: 1.6; color: #222;
  }}
  h1 {{ border-bottom: 2px solid #444; padding-bottom: .3rem; }}
  h2 {{ margin-top: 1.8rem; border-bottom: 1px solid #999; padding-bottom: .2rem; }}
  h1,h2,h3 {{ line-height: 1.25; }}
  code {{ background: rgba(0,0,0,.08); padding: .1em .35em; border-radius: 4px; }}
  pre {{ background: rgba(0,0,0,.06); padding: .8rem; border-radius: 6px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th,td {{ border: 1px solid #999; padding: .4rem .6rem; text-align: left; }}
  th {{ background: rgba(0,0,0,.06); }}
  li {{ margin: .25rem 0; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #e8e8e8; }}
    code, pre {{ background: rgba(255,255,255,.1); }}
    th {{ background: rgba(255,255,255,.1); }}
    h1,h2 {{ border-color: #777; }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

out.write_text(html, encoding="utf-8")
print("wrote", out)
