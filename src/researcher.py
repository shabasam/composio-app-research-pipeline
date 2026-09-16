import os
import re
import time
from typing import Any, List, Optional, Tuple

from dotenv import load_dotenv
from exa_py import Exa
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.common import (
    confidence_label,
    classify_source,
    get_official_domains,
    is_official_url,
    normalize_domain,
)
from src.models import AppResearch, Evidence, ResearchExtraction

load_dotenv()

MODEL = os.getenv("GEMINI_RESEARCH_MODEL", "gemini-3.1-flash-lite-preview")
RESULTS_PER_QUERY = int(os.getenv("EXA_RESULTS_PER_QUERY", "5"))
MAX_SOURCES = int(os.getenv("RESEARCH_MAX_SOURCES", "18"))
MAX_SOURCE_CHARS = int(os.getenv("RESEARCH_MAX_SOURCE_CHARS", "3200"))
REQUEST_INTERVAL = float(os.getenv("EXA_REQUEST_INTERVAL", "0.15"))
MAX_RETRIES = int(os.getenv("EXA_MAX_RETRIES", "3"))


def get_official_domain(app_name: str, website: Optional[str] = None) -> Optional[str]:
    return normalize_domain(website or "") or None


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return value.__dict__
    return {}


def _clean_text(value: Any, limit: int) -> str:
    if isinstance(value, list):
        value = "\n".join(str(x) for x in value if x)
    text = " ".join(str(value or "").split())
    return text[:limit]


class Researcher:
    """Evidence-first researcher using Exa Search + Gemini structured output.

    The retrieval layer is deliberately compact: a few broad searches collect
    the evidence needed for both the rich human-readable record and the legacy
    normalized fields used by the analyzer.
    """

    SEARCH_QUERIES = [
        (
            "core_api",
            "{name} official developer API documentation authentication credentials API overview",
        ),
        (
            "pricing_access",
            "{name} official API pricing access plans limits developer charges credentials",
        ),
        (
            "api_surface",
            "{name} official API reference REST GraphQL SOAP webhooks WebSocket capabilities resources integrations",
        ),
    ]
    MCP_QUERY = "{name} MCP Model Context Protocol server official community GitHub"

    def __init__(self, exa_api_key=None, gemini_api_key=None):
        load_dotenv()
        self.exa_api_key = exa_api_key or os.getenv("EXA_API_KEY")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        if not self.exa_api_key or not self.gemini_api_key:
            raise ValueError("EXA_API_KEY and GEMINI_API_KEY are required")

        self.exa = Exa(api_key=self.exa_api_key)
        self.llm = ChatGoogleGenerativeAI(
            google_api_key=self.gemini_api_key,
            model=MODEL,
            temperature=0,
            max_output_tokens=3800,
        )
        self.structured_llm = self.llm.with_structured_output(ResearchExtraction)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(x in text for x in ("rate limit", "too many requests", "429", "qps", "quota"))

    def _search(
        self,
        query: str,
        official_domains: List[str],
        official_only: bool,
        field: str,
    ) -> List[dict]:
        kwargs = {
            "type": "auto",
            "num_results": RESULTS_PER_QUERY,
            "contents": {"highlights": True},
        }
        if official_only and official_domains:
            kwargs["include_domains"] = official_domains

        response = None
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.exa.search(query, **kwargs)
                break
            except Exception as exc:
                last_error = exc
                if attempt == MAX_RETRIES:
                    break
                time.sleep(max(0.5, REQUEST_INTERVAL * 8) * attempt if self._is_rate_limit_error(exc) else attempt)

        if response is None:
            print(f"[Researcher] Exa search failed [{field}]: {last_error}")
            return []

        data = _as_dict(response)
        raw_results = data.get("results") or getattr(response, "results", []) or []
        out = []

        for raw in raw_results:
            item = _as_dict(raw)
            url = item.get("url", "")
            if not url:
                continue
            if official_only and official_domains and not is_official_url(url, official_domains):
                continue

            highlights = item.get("highlights") or []
            text = item.get("text") or item.get("snippet") or ""
            content = _clean_text(highlights if highlights else text, MAX_SOURCE_CHARS)
            if not content:
                continue

            out.append({
                "url": url,
                "title": item.get("title", "") or "",
                "content": content,
                "source_type": classify_source(url, official_domains),
                "research_field": field,
                "query": query,
                "score": float(item.get("score", 0) or 0),
            })
        return out

    def search_sources(self, app_name: str, category: str, website: Optional[str]) -> List[dict]:
        official_domains = get_official_domains(app_name, website)
        collected: dict[str, dict] = {}

        # Three broad official searches. This is intentionally not one search
        # per schema field; Gemini will synthesize the detailed record from the
        # returned evidence.
        for field, template in self.SEARCH_QUERIES:
            query = template.format(name=app_name)
            for item in self._search(query, official_domains, True, field):
                existing = collected.get(item["url"])
                if existing is None or item["score"] > existing.get("score", 0):
                    collected[item["url"]] = item
            time.sleep(REQUEST_INTERVAL)

        # One open-web MCP query. Results are split deterministically by
        # first-party domain ownership so official MCP can be distinguished
        # from community implementations.
        mcp_query = self.MCP_QUERY.format(name=app_name)
        for item in self._search(mcp_query, official_domains, False, "mcp_search"):
            if official_domains and is_official_url(item["url"], official_domains):
                item["research_field"] = "mcp_official"
                item["mcp_track"] = "official"
            else:
                item["research_field"] = "mcp_third_party"
                item["mcp_track"] = "third_party"
            existing = collected.get(item["url"])
            if existing is None or item["score"] > existing.get("score", 0):
                collected[item["url"]] = item

        sources = list(collected.values())
        selected, seen = [], set()

        # Keep strong representation of each evidence pool.
        for field in ("core_api", "pricing_access", "api_surface"):
            candidates = [s for s in sources if s.get("research_field") == field]
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            for item in candidates[:5]:
                if item["url"] not in seen:
                    selected.append(item)
                    seen.add(item["url"])

        for field in ("mcp_official", "mcp_third_party"):
            candidates = [s for s in sources if s.get("research_field") == field]
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            for item in candidates[:4]:
                if item["url"] not in seen:
                    selected.append(item)
                    seen.add(item["url"])

        remaining = [s for s in sources if s["url"] not in seen]
        remaining.sort(
            key=lambda x: (
                1 if x.get("source_type") != "third_party" else 0,
                x.get("score", 0),
            ),
            reverse=True,
        )
        selected.extend(remaining[: max(0, MAX_SOURCES - len(selected))])
        return selected[:MAX_SOURCES]

    @staticmethod
    def build_prompt(app_name: str, category: str, sources: List[dict]) -> str:
        blocks = []
        for i, source in enumerate(sources, 1):
            blocks.append(
                f"""SOURCE {i}
FIELD: {source.get('research_field')}
MCP TRACK: {source.get('mcp_track', 'n/a')}
TITLE: {source.get('title', '')}
URL: {source.get('url', '')}
SOURCE TYPE: {source.get('source_type', 'unknown')}
CONTENT:
{source.get('content', '')}
"""
            )
        source_text = "\n".join(blocks)

        return f"""
You are an evidence-first software integration research agent.

APP: {app_name}
CATEGORY: {category}

Produce a concise, human-readable research profile AND normalized fields for
cross-app analysis. Use ONLY the supplied sources. Do not use memory, guesses,
or generic assumptions.

RICH FIELDS
1. one_liner: one factual sentence describing the product/API relevance.
2. self_serve: classify how a normal developer can get started/obtain access:
   free_self_serve | trial_self_serve | paid_plan_required |
   admin_approval_required | partner_gated | contact_sales | unclear |
   not_applicable
3. self_serve_notes: 1-3 concise sentences explaining the actual credential/
   account/app setup path, including any meaningful gate.
4. api_surface: 2-5 concise sentences covering documented API styles, major
   resource/function areas, current-vs-legacy distinctions, and important
   limits/restrictions when directly evidenced.
5. mcp_status: official_mcp | community_mcp | none_found.
6. buildability_verdict: ready_today | ready_with_workaround | blocked.
7. buildability_notes: concise practical explanation of why the integration
   can or cannot be built today.
8. blocker: material evidence-backed blocker or null. main_blocker is the
   normalized legacy alias and should contain the same value.

NORMALIZED FIELDS
- auth_methods: explicit authentication mechanisms only.
- credential_access: self_serve | trial | paid_required | admin_required |
  partner_gated | contact_sales | unknown.
- api_pricing: free | freemium | paid | usage_based | unknown.
  This means API/developer access or API-usage cost, NOT SaaS pricing,
  transaction fees, or quotas unless explicitly described as API charges.
- api_access_model: included_in_plan | plan_dependent | separate_purchase |
  usage_limited | free_access | unknown.
- api_type: explicit protocols/interfaces such as REST, GraphQL, SOAP, gRPC,
  Webhooks, WebSocket, Pub/Sub. MCP MUST NOT appear here.
  Do NOT label an API REST merely because it uses HTTP/JSON. Include REST only
  when the documentation explicitly describes that API as REST/RESTful.
- api_breadth: broad | moderate | limited | none | unknown.
- mcp_available: official | third_party | not_found | unknown.
- buildability: buildable | buildable_with_constraints | blocked | unknown.

CANONICAL ALIAS RULES
self_serve maps to credential_access as follows:
free_self_serve→self_serve, trial_self_serve→trial,
paid_plan_required→paid_required, admin_approval_required→admin_required,
partner_gated→partner_gated, contact_sales→contact_sales,
unclear/not_applicable→unknown.
mcp_status maps to mcp_available: official_mcp→official,
community_mcp→third_party, none_found→not_found.
buildability_verdict maps to buildability: ready_today→buildable,
ready_with_workaround→buildable_with_constraints, blocked→blocked.
Keep the pairs consistent.

MCP RULE
official_mcp/official requires evidence that the company itself provides or
maintains the implementation. A third-party GitHub/npm/App Store/community
implementation is community_mcp/third_party unless the evidence clearly proves
first-party ownership. Official domains can be different from the marketing
site (for example a dedicated developer domain), so judge first-party status by
ownership/content, not by hostname alone.

EVIDENCE
For every non-unknown important fact, include an evidence_ref using the exact
source_index supplied below. Supports must be a concise statement grounded in
that source. Never invent a URL or source index.

CONFIDENCE
0.90-1.00 only when nearly all important fields are directly supported by strong
evidence. 0.75-0.89 when most important fields are supported. 0.50-0.74 when
several fields remain uncertain. Below 0.50 when evidence is weak.

Return ONLY ResearchExtraction.

SOURCES
{source_text}
"""

    def extract(self, app_name: str, category: str, sources: List[dict]) -> ResearchExtraction:
        prompt = self.build_prompt(app_name, category, sources)
        last = None
        for attempt in range(1, 4):
            try:
                result = self.structured_llm.invoke([HumanMessage(content=prompt)])
                return result if isinstance(result, ResearchExtraction) else ResearchExtraction.model_validate(result)
            except Exception as exc:
                last = exc
                print(f"[Researcher] Extraction {attempt}/3 failed for {app_name}: {exc}")
                if any(x in str(exc).lower() for x in ("quota", "429", "resource exhausted", "rate limit")):
                    break
                time.sleep(2 * attempt)
        raise RuntimeError(f"Research extraction failed for {app_name}: {last}")

    @staticmethod
    def _credential_from_self_serve(value: str) -> str:
        return {
            "free_self_serve": "self_serve",
            "trial_self_serve": "trial",
            "paid_plan_required": "paid_required",
            "admin_approval_required": "admin_required",
            "partner_gated": "partner_gated",
            "contact_sales": "contact_sales",
            "unclear": "unknown",
            "not_applicable": "unknown",
        }.get(value, "unknown")

    @staticmethod
    def _mcp_alias(value: str) -> str:
        return {"official_mcp": "official", "community_mcp": "third_party", "none_found": "not_found"}.get(value, "unknown")

    @staticmethod
    def _build_alias(value: str) -> str:
        return {"ready_today": "buildable", "ready_with_workaround": "buildable_with_constraints", "blocked": "blocked"}.get(value, "unknown")

    def build_result(self, extraction: ResearchExtraction, sources: List[dict]) -> AppResearch:
        evidence = []
        for ref in extraction.evidence_refs:
            if 1 <= ref.source_index <= len(sources):
                src = sources[ref.source_index - 1]
                evidence.append(
                    Evidence(
                        url=src["url"],
                        title=src.get("title"),
                        source_type=src.get("source_type", "unknown"),
                        field=ref.field,
                        supports=ref.supports,
                    )
                )

        self_serve = extraction.self_serve
        mcp_status = extraction.mcp_status
        buildability_verdict = extraction.buildability_verdict

        # Rich fields are canonical; normalized legacy aliases are derived
        # deterministically so they cannot drift apart.
        credential_access = self._credential_from_self_serve(self_serve)
        mcp_available = self._mcp_alias(mcp_status)
        buildability = self._build_alias(buildability_verdict)

        evidence_fields = {e.field for e in evidence}
        important_values = {
            "auth_methods": extraction.auth_methods,
            "credential_access": credential_access,
            "self_serve": self_serve,
            "api_pricing": extraction.api_pricing,
            "api_access_model": extraction.api_access_model,
            "api_type": extraction.api_type,
            "api_breadth": extraction.api_breadth,
            "mcp_available": mcp_available,
            "mcp_status": mcp_status,
            "buildability": buildability,
            "buildability_verdict": buildability_verdict,
        }
        def is_unresolved(value):
            if value is None:
                return True
            if isinstance(value, list):
                return len(value) == 0
            if isinstance(value, str):
                return value in {"unknown", "unclear", "none_found"}
            return False

        unresolved = sum(
            is_unresolved(value) for value in important_values.values()
        )

        # Evidence-backed API protocol guard: do not call a generic HTTP API
        # REST unless the supporting source explicitly uses REST/RESTful
        # terminology. Slack's Web API, for example, is HTTP RPC-style rather
        # than REST.
        api_type = list(extraction.api_type or [])
        api_type_evidence_text = " ".join(
            e.supports.lower() for e in evidence if e.field == "api_type"
        )
        explicit_rest = bool(
            re.search(r"\brest(?:ful)?\b", api_type_evidence_text)
        )
        if "REST" in api_type and not explicit_rest:
            api_type = [x for x in api_type if x != "REST"]
        confidence = float(extraction.confidence)
        if unresolved >= 3:
            confidence = min(confidence, 0.78)
        elif unresolved >= 1:
            confidence = min(confidence, 0.89)
        if extraction.api_pricing == "unknown":
            confidence = min(confidence, 0.89)
        if not evidence_fields:
            confidence = min(confidence, 0.50)

        return AppResearch(
            app=extraction.app,
            category=extraction.category,
            description=extraction.description,
            one_liner=extraction.one_liner,
            auth_methods=extraction.auth_methods,
            self_serve=self_serve,
            self_serve_notes=extraction.self_serve_notes,
            api_surface=extraction.api_surface,
            credential_access=credential_access,
            api_pricing=extraction.api_pricing,
            api_access_model=extraction.api_access_model,
            api_type=api_type,
            api_breadth=extraction.api_breadth,
            mcp_available=mcp_available,
            mcp_status=mcp_status,
            buildability=buildability,
            buildability_verdict=buildability_verdict,
            buildability_notes=extraction.buildability_notes,
            blocker=extraction.blocker,
            main_blocker=extraction.blocker,
            evidence=evidence,
            evidence_urls=list(dict.fromkeys(e.url for e in evidence)),
            confidence=round(confidence, 2),
            confidence_label=confidence_label(confidence),
            research_notes=extraction.research_notes,
        )

    def research_app(
        self,
        app_name: str,
        category: str,
        website: Optional[str],
    ) -> Tuple[AppResearch, List[dict]]:
        print(f"\n[Researcher] {app_name}")
        sources = self.search_sources(app_name, category, website)
        print(f"[Researcher] {len(sources)} selected evidence sources")

        if not sources:
            return (
                AppResearch(
                    app=app_name,
                    category=category,
                    description="",
                    one_liner="",
                    auth_methods=[],
                    self_serve="unclear",
                    self_serve_notes="No usable sources found.",
                    api_surface="",
                    credential_access="unknown",
                    api_pricing="unknown",
                    api_access_model="unknown",
                    api_type=[],
                    api_breadth="unknown",
                    mcp_available="unknown",
                    mcp_status="none_found",
                    buildability="unknown",
                    buildability_verdict="blocked",
                    buildability_notes="No usable sources were retrieved.",
                    main_blocker="No usable sources found.",
                    evidence=[],
                    confidence=0.0,
                    research_notes="Research retrieval returned no usable sources.",
                ),
                [],
            )

        extraction = self.extract(app_name, category, sources)
        result = self.build_result(extraction, sources)
        return result, sources


if __name__ == "__main__":
    print("Researcher ready:", MODEL)
