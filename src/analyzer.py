import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

VERIFIED_FILE = Path("data/verified_results.json")
OUT_HTML = Path("web/case-study.html")

IMPORTANT_FIELDS = [
    "auth_methods", "credential_access", "self_serve", "self_serve_notes",
    "api_pricing", "api_access_model", "api_type", "api_breadth",
    "mcp_available", "mcp_status", "buildability", "buildability_verdict",
    "api_surface", "buildability_notes", "blocker", "main_blocker",
]

STATUS_ORDER = ["VERIFIED", "CORRECTED", "UNVERIFIED", "CONTRADICTED"]


def load_records() -> list[dict[str, Any]]:
    with VERIFIED_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def esc(value: Any) -> str:
    return html.escape(str(value))


def badge(value: Any, css: str = "") -> str:
    return f'<span class="badge {css}">{esc(value)}</span>'


def status_badge(status: str) -> str:
    cls = {
        "VERIFIED": "ok",
        "CORRECTED": "fix",
        "UNVERIFIED": "unknown",
        "CONTRADICTED": "bad",
    }.get(status, "neutral")
    return badge(status, cls)


def value_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(esc(x) for x in value) if value else "—"
    if value is None:
        return "—"
    return esc(value)


def source_links(record: dict[str, Any]) -> str:
    items = []
    for source in record.get("evidence", []) or []:
        url = str(source.get("url") or "")
        title = str(source.get("title") or url or "Evidence")
        if not url:
            continue
        items.append(
            f'<li><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a>'
            f'<span class="source-meta">{esc(source.get("source_type", ""))} · {esc(source.get("field", ""))}</span>'
            f'<div class="supports">{esc(source.get("supports", ""))}</div></li>'
        )
    return "<ul class=""evidence-list"">" + "".join(items) + "</ul>" if items else "<p class=""muted"">No evidence items recorded.</p>"


def app_detail(record: dict[str, Any]) -> str:
    ver = record.get("field_verification") or {}
    statuses = " ".join(status_badge(v) for v in ver.values())
    rows = []
    for field in [
        "auth_methods", "credential_access", "self_serve", "api_pricing",
        "api_access_model", "api_type", "api_breadth", "mcp_available",
        "mcp_status", "buildability", "buildability_verdict",
    ]:
        rows.append(
            f'<tr><td>{esc(field)}</td><td>{value_list(record.get(field))}</td>'
            f'<td>{status_badge(ver.get(field, "UNVERIFIED"))}</td></tr>'
        )
    blocker = record.get("blocker") or record.get("main_blocker") or "None recorded"
    return f'''
    <details class="app-details">
      <summary>View evidence, API profile and verification details</summary>
      <div class="detail-grid">
        <div>
          <h4>Profile</h4>
          <p>{esc(record.get("description", ""))}</p>
          <p><strong>Buildability:</strong> {esc(record.get("buildability_notes", ""))}</p>
          <p><strong>Blocker:</strong> {esc(blocker)}</p>
        </div>
        <div>
          <h4>Verification status</h4>
          <div class="chip-cloud">{statuses}</div>
          <p class="muted">Scope: {esc(record.get("verification_scope", "not_verified"))}; confidence: {esc(record.get("confidence_label", ""))} ({esc(record.get("confidence", ""))})</p>
        </div>
      </div>
      <div class="detail-grid">
        <div>
          <h4>Structured fields</h4>
          <div class="table-wrap compact"><table><thead><tr><th>Field</th><th>Value</th><th>Status</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
        </div>
        <div>
          <h4>Evidence</h4>
          {source_links(record)}
        </div>
      </div>
    </details>
    '''


def make_html(records: list[dict[str, Any]]) -> str:
    total = len(records)
    sample = [r for r in records if r.get("verification_scope") == "sample"]

    categories = Counter(r.get("category", "Unknown") for r in records)
    mcp = Counter(r.get("mcp_available", "unknown") for r in records)
    buildability = Counter(r.get("buildability", "unknown") for r in records)
    breadth = Counter(r.get("api_breadth", "unknown") for r in records)
    pricing = Counter(r.get("api_pricing", "unknown") for r in records)
    confidence = Counter(r.get("confidence_label", "unknown") for r in records)

    sample_statuses = Counter()
    sample_corrections = Counter()
    sample_unresolved = Counter()
    for record in sample:
        for field, status in (record.get("field_verification") or {}).items():
            sample_statuses[status] += 1
            if status == "CORRECTED":
                sample_corrections[field] += 1
            elif status == "UNVERIFIED":
                sample_unresolved[field] += 1

    evidence_count = sum(len(r.get("evidence", []) or []) for r in records)
    correction_count = sample_statuses.get("CORRECTED", 0)
    unresolved_count = sample_statuses.get("UNVERIFIED", 0)
    verified_count = sample_statuses.get("VERIFIED", 0)
    avg_confidence = round(sum(float(r.get("confidence", 0) or 0) for r in records) / total, 2) if total else 0
    avg_sample_confidence = round(sum(float(r.get("confidence", 0) or 0) for r in sample) / len(sample), 2) if sample else 0

    category_rows = []
    for k, v in sorted(categories.items()):
        category_rows.append(f"<tr><td>{esc(k)}</td><td>{v}</td><td>{pct(v,total)}%</td></tr>")

    correction_rows = []
    for field, count in sample_corrections.most_common():
        correction_rows.append(f"<tr><td>{esc(field)}</td><td>{count}</td></tr>")

    unresolved_rows = []
    for field, count in sample_unresolved.most_common():
        unresolved_rows.append(f"<tr><td>{esc(field)}</td><td>{count}</td></tr>")

    app_rows = []
    for idx, r in enumerate(records, 1):
        status = "sample" if r.get("verification_scope") == "sample" else "not_verified"
        ver = r.get("field_verification") or {}
        status_counts = Counter(ver.values())
        status_summary = " ".join(
            badge(f"{k} {v}", {"VERIFIED":"ok", "CORRECTED":"fix", "UNVERIFIED":"unknown", "CONTRADICTED":"bad"}.get(k, "neutral"))
            for k, v in status_counts.items()
        )
        app_rows.append(f'''
        <tr class="app-row" data-name="{esc(r.get('app',''))}" data-category="{esc(r.get('category',''))}" data-mcp="{esc(r.get('mcp_available',''))}" data-buildability="{esc(r.get('buildability',''))}" data-scope="{status}">
          <td class="num">{idx}</td>
          <td><div class="app-name">{esc(r.get('app',''))}</div><div class="app-one-liner">{esc(r.get('one_liner',''))}</div></td>
          <td>{badge(r.get('category',''))}</td>
          <td>{value_list(r.get('api_type'))}</td>
          <td>{badge(r.get('mcp_available','unknown'), 'mcp-' + str(r.get('mcp_available','unknown')))}</td>
          <td>{badge(r.get('buildability','unknown'))}</td>
          <td><div class="confidence">{esc(r.get('confidence', ''))} <span>{esc(r.get('confidence_label',''))}</span></div><div class="status-line">{status_summary}</div></td>
          <td>{app_detail(r)}</td>
        </tr>
        ''')

    category_list = "".join(f'<div class="bar-row"><span>{esc(k)}</span><div class="bar-track"><div class="bar-fill" style="width:{pct(v,max(categories.values()) if categories else 1)}%"></div></div><strong>{v}</strong></div>' for k,v in categories.most_common())
    build_list = "".join(f'<div class="bar-row"><span>{esc(k)}</span><div class="bar-track"><div class="bar-fill alt" style="width:{pct(v,max(buildability.values()) if buildability else 1)}%"></div></div><strong>{v}</strong></div>' for k,v in buildability.most_common())
    breadth_list = "".join(f'<div class="bar-row"><span>{esc(k)}</span><div class="bar-track"><div class="bar-fill third" style="width:{pct(v,max(breadth.values()) if breadth else 1)}%"></div></div><strong>{v}</strong></div>' for k,v in breadth.most_common())
    pricing_list = "".join(f'<div class="bar-row"><span>{esc(k)}</span><div class="bar-track"><div class="bar-fill fourth" style="width:{pct(v,max(pricing.values()) if pricing else 1)}%"></div></div><strong>{v}</strong></div>' for k,v in pricing.most_common())

    app_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Evidence-first research and independent verification of 100 apps across 10 categories for the Composio app research assignment.">
<title>Composio App Research — 100-App Integration Landscape</title>
<style>
:root {{
  --bg: #0a0f1b; --panel: #111827; --panel-2: #172033; --panel-3:#0e1626;
  --text: #f4f7fb; --muted:#9aa8bf; --line:#27344a; --accent:#7dd3fc;
  --good:#86efac; --warn:#fde68a; --bad:#fda4af; --fix:#c4b5fd;
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{ margin:0; background:radial-gradient(circle at 15% 0%,#142341 0,#0a0f1b 38%),var(--bg); color:var(--text); font:14px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
a {{ color:var(--accent); }}
main {{ max-width:1400px; margin:auto; padding:28px 22px 80px; }}
.hero {{ border:1px solid var(--line); border-radius:26px; padding:38px; background:linear-gradient(135deg,rgba(17,24,39,.96),rgba(18,30,52,.94)); box-shadow:0 30px 100px rgba(0,0,0,.28); }}
.eyebrow {{ text-transform:uppercase; letter-spacing:.14em; font-size:11px; font-weight:800; color:var(--accent); }}
h1 {{ font-size:clamp(34px,5vw,62px); line-height:1; margin:12px 0 18px; max-width:1000px; }}
.lead {{ max-width:980px; font-size:18px; color:var(--muted); margin:0; }}
.sublead {{ color:#c7d2e6; max-width:900px; margin-top:14px; }}
.metrics {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin-top:28px; }}
.metric {{ border:1px solid var(--line); border-radius:16px; padding:16px; background:rgba(23,32,51,.78); }}
.metric .value {{ font-size:31px; font-weight:850; line-height:1.1; }}
.metric .label {{ margin-top:6px; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }}
nav {{ position:sticky; top:0; z-index:10; backdrop-filter:blur(14px); background:rgba(10,15,27,.78); border-bottom:1px solid var(--line); margin:20px -22px 0; padding:10px 22px; display:flex; gap:16px; flex-wrap:wrap; }}
nav a {{ text-decoration:none; font-size:12px; color:#cbd5e1; }}
section {{ margin-top:38px; }}
h2 {{ font-size:26px; margin:0 0 14px; }}
h3 {{ font-size:17px; margin:0 0 10px; }}
.card-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:19px; }}
.card p {{ color:var(--muted); margin:7px 0 0; }}
.note {{ border-left:3px solid var(--accent); padding:14px 16px; border-radius:10px; background:rgba(14,22,38,.9); color:#bfcbdd; }}
.bar-row {{ display:grid; grid-template-columns:200px 1fr 42px; gap:10px; align-items:center; margin:9px 0; }}
.bar-track {{ height:10px; background:#090e18; border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
.bar-fill {{ height:100%; background:linear-gradient(90deg,#60a5fa,#7dd3fc); border-radius:999px; }}
.bar-fill.alt {{ background:linear-gradient(90deg,#34d399,#86efac); }}
.bar-fill.third {{ background:linear-gradient(90deg,#a78bfa,#c4b5fd); }}
.bar-fill.fourth {{ background:linear-gradient(90deg,#f59e0b,#fde68a); }}
.badge {{ display:inline-flex; align-items:center; gap:4px; padding:3px 7px; border-radius:999px; border:1px solid #334155; background:#152033; color:#cbd5e1; font-size:11px; white-space:nowrap; }}
.badge.ok {{ border-color:#285c43; background:#11261d; color:var(--good); }}
.badge.fix {{ border-color:#4c3d76; background:#1f1834; color:var(--fix); }}
.badge.unknown {{ border-color:#6b5720; background:#2b2411; color:var(--warn); }}
.badge.bad {{ border-color:#67303b; background:#2a131a; color:var(--bad); }}
.badge.mcp-official {{ border-color:#285c43; color:var(--good); }}
.badge.mcp-third_party {{ border-color:#4c3d76; color:var(--fix); }}
.badge.mcp-not_found {{ border-color:#475569; color:#cbd5e1; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:18px; }}
table {{ width:100%; border-collapse:collapse; min-width:1080px; background:var(--panel); }}
th,td {{ padding:12px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
th {{ position:sticky; top:45px; z-index:3; background:#111a2a; color:#dce7f8; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
.num {{ color:#64748b; width:45px; }}
.app-name {{ font-weight:800; font-size:14px; }}
.app-one-liner {{ color:var(--muted); max-width:460px; margin-top:3px; }}
.confidence {{ font-weight:800; }}
.confidence span {{ color:var(--muted); font-weight:500; font-size:11px; }}
.status-line {{ display:flex; gap:4px; flex-wrap:wrap; margin-top:7px; }}
.filters {{ display:grid; grid-template-columns:1.5fr repeat(4,1fr); gap:10px; margin:16px 0; }}
input,select {{ width:100%; border:1px solid var(--line); background:#0e1626; color:var(--text); border-radius:11px; padding:10px 11px; outline:none; }}
input:focus,select:focus {{ border-color:#4b9fc4; box-shadow:0 0 0 3px rgba(125,211,252,.08); }}
.app-details {{ min-width:330px; }}
.app-details summary {{ cursor:pointer; color:var(--accent); font-size:12px; }}
.detail-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:15px; }}
.compact table {{ min-width:0; font-size:12px; }}
.compact th {{ position:static; }}
.chip-cloud {{ display:flex; flex-wrap:wrap; gap:5px; }}
.muted {{ color:var(--muted); }}
.evidence-list {{ padding-left:18px; margin:0; }}
.evidence-list li {{ margin:8px 0; }}
.source-meta {{ display:block; color:#728198; font-size:10px; margin-top:2px; }}
.supports {{ color:#aab6ca; font-size:11px; margin-top:3px; }}
.callout {{ border:1px solid #3b4962; padding:17px; border-radius:15px; background:linear-gradient(135deg,#131d30,#0f1728); }}
.callout strong {{ color:#fff; }}
.small {{ font-size:12px; }}
footer {{ margin-top:50px; padding-top:22px; border-top:1px solid var(--line); color:var(--muted); }}
@media(max-width:1000px) {{ .metrics{{grid-template-columns:repeat(3,1fr)}} .card-grid{{grid-template-columns:1fr 1fr}} .filters{{grid-template-columns:1fr 1fr}} }}
@media(max-width:700px) {{ main{{padding:16px 14px 55px}} .hero{{padding:25px}} .metrics{{grid-template-columns:1fr 1fr}} .card-grid{{grid-template-columns:1fr}} .filters{{grid-template-columns:1fr}} .bar-row{{grid-template-columns:130px 1fr 35px}} nav{{margin-left:-14px;margin-right:-14px;padding-left:14px}} }}
</style>
</head>
<body>
<main>
  <header class="hero">
    <div class="eyebrow">Composio App Research Assignment</div>
    <h1>100-App API & Integration Landscape</h1>
    <p class="lead">An evidence-first research pipeline covering 100 apps across 10 categories, with a second-agent verification pass over a reproducible 15-app sample.</p>
    <p class="sublead">The dataset separates credential access, API pricing, API access model, API type, API breadth, MCP availability, and practical buildability instead of collapsing them into one integration score.</p>
    <div class="metrics">
      <div class="metric"><div class="value">{total}</div><div class="label">Apps researched</div></div>
      <div class="metric"><div class="value">{len(sample)}</div><div class="label">Independent sample</div></div>
      <div class="metric"><div class="value">{evidence_count}</div><div class="label">Evidence items</div></div>
      <div class="metric"><div class="value">{correction_count}</div><div class="label">Sample corrections</div></div>
      <div class="metric"><div class="value">{unresolved_count}</div><div class="label">Sample unresolved</div></div>
    </div>
  </header>

  <nav>
    <a href="#method">Method</a><a href="#verification">Verification</a><a href="#landscape">Landscape</a><a href="#mcp">MCP</a><a href="#audit">Audit examples</a><a href="#apps">100 apps</a>
  </nav>

  <section id="method">
    <h2>How the pipeline works</h2>
    <div class="card-grid">
      <div class="card"><h3>1 · Research</h3><p>Each app is queried for authentication, credential/self-serve access, API pricing, access model, surface/breadth, MCP, and buildability. Evidence is attached at field level.</p></div>
      <div class="card"><h3>2 · Verify</h3><p>A separate verification pass performs fresh searches and checks the research claim-by-claim. Missing independent evidence becomes <strong>UNVERIFIED</strong>, not an automatic failure.</p></div>
      <div class="card"><h3>3 · Guardrails</h3><p>Schema enums, REST classification, MCP provenance, rich/normalized aliases, confidence, and final 100-app coverage are enforced deterministically after model output.</p></div>
    </div>
    <div class="note" style="margin-top:14px"><strong>Evidence principle:</strong> an unverified field means the independent pass did not establish the claim. It is intentionally different from a contradiction. MCP follows an additional provenance rule: a third-party implementation cannot by itself establish an official vendor MCP.</div>
  </section>

  <section id="verification">
    <h2>Independent verification</h2>
    <div class="card-grid">
      <div class="card"><h3>{verified_count} verified field checks</h3><p>The largest share of checked fields were directly supported by independent evidence.</p></div>
      <div class="card"><h3>{correction_count} corrections</h3><p>Corrections replace the researched value only when the independent evidence supports a schema-valid alternative.</p></div>
      <div class="card"><h3>{unresolved_count} unresolved</h3><p>Unresolved fields remain visible and lower confidence; the pipeline does not manufacture certainty.</p></div>
    </div>
    <div class="detail-grid" style="margin-top:14px">
      <div class="card"><h3>Most frequently corrected fields</h3><div class="table-wrap compact"><table><thead><tr><th>Field</th><th>Corrections</th></tr></thead><tbody>{''.join(correction_rows)}</tbody></table></div></div>
      <div class="card"><h3>Most frequently unresolved fields</h3><div class="table-wrap compact"><table><thead><tr><th>Field</th><th>Unresolved</th></tr></thead><tbody>{''.join(unresolved_rows)}</tbody></table></div></div>
    </div>
    <div class="callout" style="margin-top:14px"><strong>Confidence:</strong> average across the 100 records is <strong>{avg_confidence}</strong>; the independently verified sample averages <strong>{avg_sample_confidence}</strong>. Confidence is capped when important fields remain unresolved or corrections accumulate.</div>
  </section>

  <section id="landscape">
    <h2>100-app landscape</h2>
    <div class="detail-grid">
      <div class="card"><h3>Category coverage</h3>{category_list}</div>
      <div class="card"><h3>API breadth</h3>{breadth_list}</div>
    </div>
    <div class="detail-grid" style="margin-top:14px">
      <div class="card"><h3>Buildability</h3>{build_list}</div>
      <div class="card"><h3>API pricing model</h3>{pricing_list}</div>
    </div>
  </section>

  <section id="mcp">
    <h2>MCP landscape</h2>
    <div class="card-grid">
      <div class="card"><div class="metric"><div class="value">{mcp.get('official',0)}</div><div class="label">Official</div></div><p>Records currently classify an MCP implementation as official.</p></div>
      <div class="card"><div class="metric"><div class="value">{mcp.get('third_party',0)}</div><div class="label">Third-party</div></div><p>Community/third-party MCP implementations are present without first-party proof.</p></div>
      <div class="card"><div class="metric"><div class="value">{mcp.get('not_found',0)}</div><div class="label">Not found</div></div><p>No MCP implementation was found by the research pass.</p></div>
    </div>
    <div class="note" style="margin-top:14px">MCP is kept separate from <code>api_type</code>. This matters because an app may expose REST/GraphQL APIs while also having an MCP layer, and the two should not be treated as the same protocol.</div>
  </section>

  <section id="audit">
    <h2>Where verification changed the research</h2>
    <div class="card-grid">
      <div class="card"><h3>DataForSEO</h3><p>The final record marks multiple fields as corrected, including MCP fields. The evidence set contains first-party API, pricing, breadth, and MCP documentation.</p></div>
      <div class="card"><h3>Jira</h3><p>The verification pass corrected four fields and retained a clear evidence trail for REST, authentication, GraphQL, and rate limiting.</p></div>
      <div class="card"><h3>Pylon</h3><p>This case produced many corrections and several unresolved fields, making it a useful example of why the system needs independent checking instead of confirmation-only logic.</p></div>
    </div>
    <div class="callout" style="margin-top:14px"><strong>Known limitation in this sample:</strong> Google Ads and Meta Ads retain unresolved MCP/pricing-related fields because the retrieved evidence did not satisfy the current first-party provenance rule. The system deliberately preserves that uncertainty rather than converting third-party references into official claims.</div>
  </section>

  <section id="apps">
    <h2>Complete app inventory</h2>
    <p class="muted">Search and filter the final 100 records. Open a row to inspect structured values, field-level verification, and evidence URLs.</p>
    <div class="filters">
      <input id="search" placeholder="Search app name or description…" autocomplete="off">
      <select id="category"><option value="">All categories</option>{''.join(f'<option>{esc(k)}</option>' for k in sorted(categories))}</select>
      <select id="mcpFilter"><option value="">All MCP</option><option value="official">Official</option><option value="third_party">Third-party</option><option value="not_found">Not found</option></select>
      <select id="buildFilter"><option value="">All buildability</option><option value="buildable">Buildable</option><option value="buildable_with_constraints">With constraints</option><option value="blocked">Blocked</option></select>
      <select id="scopeFilter"><option value="">All verification scope</option><option value="sample">Verified sample</option><option value="not_verified">Not independently sampled</option></select>
    </div>
    <div id="resultCount" class="small muted" style="margin:8px 0 10px"></div>
    <div class="table-wrap">
      <table id="appsTable">
        <thead><tr><th>#</th><th>App</th><th>Category</th><th>API type</th><th>MCP</th><th>Buildability</th><th>Confidence / verification</th><th>Details</th></tr></thead>
        <tbody>{''.join(app_rows)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Category roll-up</h2>
    <div class="table-wrap"><table><thead><tr><th>Category</th><th>Apps</th><th>Share</th></tr></thead><tbody>{''.join(category_rows)}</tbody></table></div>
  </section>

  <footer>
    <strong>Composio App Research Pipeline</strong><br>
    100 researched apps · 15 independently verified sample records · {evidence_count} evidence items · generated as a standalone HTML page.<br>
    The embedded dataset is derived from the final verification output; the repository remains the source of the executable pipeline and raw audit artifacts.
  </footer>
</main>
<script>
const rows = Array.from(document.querySelectorAll('#appsTable .app-row'));
const search = document.getElementById('search');
const category = document.getElementById('category');
const mcpFilter = document.getElementById('mcpFilter');
const buildFilter = document.getElementById('buildFilter');
const scopeFilter = document.getElementById('scopeFilter');
const resultCount = document.getElementById('resultCount');
function norm(s) {{ return (s || '').toLowerCase().trim(); }}
function applyFilters() {{
  const q = norm(search.value), c = norm(category.value), m = norm(mcpFilter.value), b = norm(buildFilter.value), sc = norm(scopeFilter.value);
  let visible = 0;
  rows.forEach(row => {{
    const hay = norm(row.innerText);
    const ok = (!q || hay.includes(q)) && (!c || norm(row.dataset.category) === c) && (!m || norm(row.dataset.mcp) === m) && (!b || norm(row.dataset.buildability) === b) && (!sc || norm(row.dataset.scope) === sc);
    row.style.display = ok ? '' : 'none'; if (ok) visible++;
  }});
  resultCount.textContent = `${{visible}} of ${{rows.length}} apps shown`;
}}
[search, category, mcpFilter, buildFilter, scopeFilter].forEach(el => el.addEventListener('input', applyFilters));
applyFilters();
</script>
</body></html>'''


def generate() -> None:
    records = load_records()
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(make_html(records), encoding="utf-8")
    print(f"Generated {OUT_HTML} ({len(records)} apps)")


if __name__ == "__main__":
    generate()
