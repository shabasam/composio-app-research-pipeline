# Composio App Research Pipeline — Final Version

## What this solves

This version keeps the existing research + independent verification architecture, but adds deterministic guardrails around the failure modes found during the five-app test:

- compact evidence research with Exa Search instead of provider-specific field-by-field over-searching
- separate official and third-party MCP retrieval tracks
- API pricing separated from API access model
- credential access separated from API availability
- MCP can never appear inside `api_type`
- LLM enum hallucinations are rejected after generation
- `INSUFFICIENT` no longer becomes a false correction
- every final record exposes `field_verification`
- rich research fields (`one_liner`, `self_serve`, `self_serve_notes`, `api_surface`, `mcp_status`, `buildability_verdict`, `buildability_notes`, and `blocker`) are retained alongside normalized comparison fields
- the final dataset can contain all 100 researched apps while clearly identifying the independently verified sample
- raw research, verification records, and correction logs remain auditable

## Project structure

```text
composio-app-research-agent/
├── data/
│   ├── apps.json
│   ├── apps_smoke.json
│   ├── research_raw.json
│   ├── verification.json
│   ├── verified_results.json
│   └── verification_log.json
├── src/
│   ├── models.py
│   ├── common.py
│   ├── researcher.py
│   ├── verifier.py
│   ├── batch_runner.py
│   └── analyzer.py
├── tests/
│   └── test_schema.py
├── web/
│   └── case-study.html
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Setup

```powershell
cd C:\App_Researcher
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add `EXA_API_KEY` and `GEMINI_API_KEY`. The retrieval layer uses Exa Search; Gemini performs structured extraction and independent verification.

## 1. Smoke test — five apps

The included `apps_smoke.json` uses the five apps already tested:

```powershell
python -m src.batch_runner --apps-file data/apps_smoke.json --limit 5 --verify-sample 5 --reset
```

Then inspect:

```text
data/research_raw.json
data/verification.json
data/verification_log.json
data/verified_results.json
```

## 2. Full 100-app research + independent sample verification

```powershell
python -m src.batch_runner --apps-file data/apps.json --verify-sample 15 --seed 42 --reset
```

This does two passes:

1. Research all apps.
2. Independently verify an adaptive, reproducible sample.

The final `verified_results.json` contains all researched apps. Sample-verified rows have `verification_scope = "sample"` and field-level statuses such as `VERIFIED`, `CORRECTED`, or `UNVERIFIED`. Non-sample rows are retained with `verification_scope = "not_verified"` and all field statuses `UNVERIFIED`.

## 3. Research-only mode

```powershell
python -m src.batch_runner --apps-file data/apps.json --research-only --reset
```

## 4. Generate the case study HTML

```powershell
python -m src.analyzer
```

Open:

```text
web/case-study.html
```


## Rich record contract

Each app record now preserves both the human-readable audit profile and normalized fields used for cross-app analysis. The richer portion follows this shape:

```json
{
  "name": "Shopify",
  "category": "Ecommerce",
  "one_liner": "...",
  "auth_methods": ["OAuth 2.0"],
  "self_serve": "free_self_serve",
  "self_serve_notes": "...",
  "api_surface": "...",
  "mcp_status": "official_mcp",
  "buildability_verdict": "ready_today",
  "blocker": null,
  "evidence_urls": ["..."],
  "confidence": 0.89,
  "confidence_label": "medium"
}
```

The legacy normalized fields remain in the same record (`credential_access`, `api_pricing`, `api_access_model`, `api_type`, `api_breadth`, `mcp_available`, and `buildability`) so the analyzer and cross-app summaries continue to work.

## Verification semantics

### SUPPORTED
Independent evidence directly supports the original value.

### CORRECTED
Independent evidence supports a different, schema-valid value.

### INSUFFICIENT
Independent retrieval did not establish the field. This is **not** treated as proof that the original research was false. The original value remains in the final row but is marked `UNVERIFIED`.

### CONTRADICTED
Independent evidence directly conflicts with the original claim. If a valid replacement is supported, that replacement is applied.

## Deterministic safety layer

The verifier LLM is not trusted to invent taxonomy values.

Examples:

```text
api_pricing = "free_with_limitations"
```

is not allowed because the taxonomy only permits:

```text
free
freemium
paid
usage_based
unknown
```

Likewise:

```text
credential_access = "plan_dependent"
```

is rejected because that value belongs to `api_access_model`, not `credential_access`.

Invalid LLM corrections are converted to `INSUFFICIENT` rather than silently corrupting the final dataset.

## MCP handling

MCP discovery is intentionally split:

```text
Official MCP search
       +
Third-party MCP search
       ↓
independent evidence
```

The verifier receives those evidence groups separately. First-party developer domains may differ from the marketing domain (for example `shopify.dev`), so the code maintains app-specific official-domain aliases. A third-party GitHub repository therefore cannot make an MCP implementation appear official when an official company implementation has not been established.

MCP is also kept out of `api_type`.

## Evidence

Research evidence is field-aware:

```json
{
  "url": "...",
  "title": "...",
  "source_type": "official_developer",
  "field": "auth_methods",
  "supports": "Confirms OAuth 2.0 authentication."
}
```

The researcher is instructed to attach evidence to important non-unknown fields. The final dataset therefore contains a traceable evidence trail rather than only an LLM-generated answer.

## What to report in the assignment

Do not invent accuracy numbers.

Use the generated `verification.json` and `verification_log.json` to calculate:

- number of independently verified apps
- number of fields independently checked
- first-pass supported fields
- corrected fields
- unresolved fields
- post-correction agreement

The supplied `analyzer.py` generates coverage and verification metrics directly from the files.

## Important limitation

The 100-app catalog is an initial operational list. For obscure apps, especially entries without a known website, the researcher uses open-web queries instead of pretending an incorrect official domain is authoritative. Before submission, manually inspect the `apps.json` website for any obscure app whose official domain remains uncertain.

## Repository hygiene

Never commit `.env` or API keys.

The output JSON files are ignored by Git by default so you can decide whether to include them as assignment artifacts separately.

## Current final snapshot

The included `data/verified_results.json` is the 100-record final dataset snapshot used to generate `web/case-study.html`.

Snapshot metrics:

- 100 apps across 10 categories
- 15 independently verified sample records
- 511 recorded evidence items
- 39 corrected fields in the verified sample
- 22 unresolved fields in the verified sample
- 94 buildable, 5 buildable with constraints, 1 blocked
- 67 official MCP, 29 third-party MCP, 4 not found in the full dataset

The HTML page is self-contained and includes search/filter controls plus expandable per-app evidence and verification details.
