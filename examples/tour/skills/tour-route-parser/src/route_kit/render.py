"""Render route JSON into self-contained detail pages and a quality index.

The page template (``templates/route.html``) holds all layout and behaviour; this module
only embeds the data. Pages load no external resources.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _embed(data: dict) -> str:
    # No "<" survives inside the script block: no </script>, no <!--.
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def page(content: dict, cover: bytes | None = None, *, audience: str = "review") -> str:
    """The detail page; ``cover`` (JPEG) is embedded as the hero picture.

    ``audience="customer"`` shows only the customer view, for published content that carries
    no source evidence (a sales projection).
    """
    template = (TEMPLATES / "route.html").read_text()
    head, _, rest = template.partition("__TITLE__")
    body, _, tail = rest.partition("__DATA__")
    title = html.escape(content.get("title") or "线路详情")
    data = dict(content)
    if audience == "customer":
        data["audience"] = "customer"
    if cover:
        data["cover_data"] = "data:image/jpeg;base64," + base64.b64encode(cover).decode()
    return head + title + body + _embed(data) + tail


def write_route(path: Path, content: dict, cover: bytes | None = None) -> None:
    path.write_text(page(content, cover))


def write_missing(path: Path, entry: dict) -> None:
    path.write_text(
        page(
            {
                "missing": True,
                "title": entry.get("name", ""),
                "name": entry.get("name", ""),
                "code": entry.get("code", ""),
            }
        )
    )


def write_index(path: Path, summaries: list[dict]) -> None:
    rows = []
    for s in summaries:
        link = html.escape(f"routes/{s['code']}.html", quote=True)
        if s.get("status") == "ok":
            units = s.get("units") or 0
            cov = round(100 * s.get("direct", s["mapped"]) / units) if units else 0
            total = round(100 * s["mapped"] / units) if units else 0
            days = f"{s['days_extracted']}/{s['days_found']}"
            listed = s.get("days")
            flag = (
                ""
                if not listed or listed == s["days_found"]
                else f"<span class='w'>登记 {html.escape(str(listed))} 天</span>"
            )
            rows.append(
                f"<tr><td><a href='{link}'>{html.escape(s.get('title') or s.get('name', ''))}</a><div class='c'>{html.escape(s['code'])} · {html.escape(s.get('file', ''))}</div></td>"
                f"<td>{days}{flag}</td><td>{cov}%<div class='c'>含推断 {total}%</div></td>"
                f"<td>{s['issues']}</td><td>{s.get('auto_attached') or ''}</td>"
                f"<td>{s['risky_unmapped'] or ''}</td></tr>"
            )
        else:
            name = html.escape(s.get("name", ""))
            # Only a missing attachment has a page; a file that failed to process has none.
            title = f"<a href='{link}'>{name}</a>" if s.get("status") == "no_attachment" else name
            rows.append(
                f"<tr><td>{title}<div class='c'>{html.escape(s['code'])}</div></td>"
                f"<td colspan='5' class='w'>{'缺少附件' if s.get('status') == 'no_attachment' else html.escape(s.get('error', '无法读取'))}</td></tr>"
            )
    ok = [s for s in summaries if s.get("status") == "ok"]
    total_units = sum(s["units"] for s in ok) or 1
    stats = (
        f"{len(summaries)} 条线路 · 生成 {len(ok)} 个详情页 · "
        f"整理成功 {sum(s['days_extracted'] for s in ok)}/{sum(s['days_found'] for s in ok)} 天 · "
        f"原文直接引用 {round(100 * sum(s.get('direct', s['mapped']) for s in ok) / total_units)}% · "
        f"原样挂入待审 {sum(s.get('auto_attached', 0) for s in ok)} 条"
    )
    path.write_text(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>线路详情案例</title><style>
:root{{--ground:#f4f2ec;--card:#fff;--ink:#23332e;--soft:#5d6f68;--line:rgba(35,51,46,.13);--accent:#1f7a8c;--warn:#9a6b12}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--ground:#141b19;--card:#1c2522;--ink:#e4ebe8;--soft:#9aaba5;--line:rgba(228,235,232,.12);--accent:#5fb4c4;--warn:#e0b25a}}}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;padding:20px 16px}}
main{{max-width:980px;margin:0 auto}}h1{{font-size:22px;margin:0 0 4px}}p{{color:var(--soft);margin:0 0 14px;font-size:14px}}
.tw{{overflow-x:auto;background:var(--card);border-radius:14px}}table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;font-weight:500;color:var(--soft);font-size:12px;padding:10px 12px;border-bottom:1px solid var(--line)}}
td{{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}}a{{color:var(--accent);text-decoration:none;font-weight:600}}
.c{{font-size:12px;color:var(--soft)}}.w{{color:var(--warn);font-size:12px;display:block}}
</style></head><body><main><h1>线路详情案例</h1><p>{stats}。页面由供应商附件自动整理，仅供内部评审，发布前须人工核对。</p>
<div class="tw"><table><tr><th>线路</th><th>整理天数</th><th>原文直接引用</th><th>问题项</th><th>原样挂入待审</th><th>高风险未归入</th></tr>{"".join(rows)}</table></div></main></body></html>""")
