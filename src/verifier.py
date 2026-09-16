import json
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from exa_py import Exa
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.common import (
    _source_proves_first_party_mcp,
    enforce_mcp_consistency,
    get_official_domains,
    is_official_url,
    normalize_domain,
    classify_source,
    sanitize_verification,
)
from src.models import AppResearch, VerificationResult

load_dotenv()

MODEL = os.getenv("GEMINI_VERIFY_MODEL", "gemini-3.1-flash-lite-preview")
RESULTS_PER_QUERY = int(os.getenv("EXA_VERIFY_RESULTS_PER_QUERY", "5"))
MAX_SOURCES = int(os.getenv("VERIFY_MAX_SOURCES", "20"))
MAX_CHARS = int(os.getenv("VERIFY_MAX_SOURCE_CHARS", "3200"))
REQUEST_INTERVAL = float(os.getenv("EXA_REQUEST_INTERVAL", "0.15"))
MAX_RETRIES = int(os.getenv("EXA_MAX_RETRIES", "3"))


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
    return " ".join(str(value or "").split())[:limit]


class Verifier:
    """Independent second-pass verifier using fresh Exa searches."""

    SEARCH_QUERIES = [
        (
            "verification_core",
            "{name} official developer API authentication credentials documentation",
        ),
        (
            "verification_access",
            "{name} official API pricing access plans limits charges developer",
        ),
        (
            "verification_surface",
            "{name} official API reference protocols capabilities resources integrations",
        ),
    ]
    MCP_QUERY = "{name} MCP Model Context Protocol server official GitHub community"

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
            max_output_tokens=6000,
        )
        self.structured_llm = self.llm.with_structured_output(VerificationResult)

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
            print(f"[Verifier] Exa search failed [{field}]: {last_error}")
            return []

        data = _as_dict(response)
        raw_results = data.get("results") or getattr(response, "results", []) or []
        results = []

        for raw in raw_results:
            item = _as_dict(raw)
            url = item.get("url", "")
            if not url:
                continue
            if official_only and official_domains and not is_official_url(url, official_domains):
                continue

            highlights = item.get("highlights") or []
            text = item.get("text") or item.get("snippet") or ""
            content = _clean_text(highlights if highlights else text, MAX_CHARS)
            if not content:
                continue

            # Preserve first-party provenance even for open-web MCP searches.
            # The previous implementation labeled every open-web result as
            # third_party, which caused valid first-party MCP pages on domains
            # such as developers.hubspot.com or docs.stripe.com to fail the
            # ownership check later in common._source_proves_first_party_mcp().
            if is_official_url(url, official_domains):
                source_type = classify_source(url, official_domains)
            else:
                source_type = "third_party"

            results.append({
                "url": url,
                "title": item.get("title", "") or "",
                "content": content,
                "source_type": source_type,
                "field": field,
                "score": float(item.get("score", 0) or 0),
            })
        return results

    def independent_search(
        self,
        app: Dict[str, Any],
        website: Optional[str],
    ) -> List[dict]:
        name = app.get("app", "")
        official_domains = get_official_domains(name, website)
        collected: Dict[str, dict] = {}

        for field, template in self.SEARCH_QUERIES:
            query = template.format(name=name)
            for item in self._search(query, official_domains, True, field):
                existing = collected.get(item["url"])
                if existing is None or item["score"] > existing.get("score", 0):
                    collected[item["url"]] = item
            time.sleep(REQUEST_INTERVAL)

        # MCP is intentionally open-web. The result is split by official-domain
        # aliases so both first-party and third-party evidence remain possible.
        mcp_query = self.MCP_QUERY.format(name=name)
        for item in self._search(mcp_query, official_domains, False, "mcp_search"):
            if official_domains and is_official_url(item["url"], official_domains):
                item["field"] = "mcp_official"
                item["mcp_track"] = "official"
                # A hostname match is only provenance, not enough by itself for
                # official ownership; the LLM must still read the content.
            else:
                item["field"] = "mcp_third_party"
                item["mcp_track"] = "third_party"
            existing = collected.get(item["url"])
            if existing is None or item["score"] > existing.get("score", 0):
                collected[item["url"]] = item

        sources = list(collected.values())
        selected, seen = [], set()

        for field in ("verification_core", "verification_access", "verification_surface"):
            candidates = [x for x in sources if x["field"] == field]
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            for item in candidates[:5]:
                if item["url"] not in seen:
                    selected.append(item)
                    seen.add(item["url"])

        for field in ("mcp_official", "mcp_third_party"):
            candidates = [x for x in sources if x["field"] == field]
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            for item in candidates[:4]:
                if item["url"] not in seen:
                    selected.append(item)
                    seen.add(item["url"])

        remaining = [x for x in sources if x["url"] not in seen]
        remaining.sort(
            key=lambda x: (1 if x.get("source_type") != "third_party" else 0, x.get("score", 0)),
            reverse=True,
        )
        selected.extend(remaining[: max(0, MAX_SOURCES - len(selected))])
        return selected[:MAX_SOURCES]

    @staticmethod
    def safe_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)

    def build_prompt(self, app: Dict[str, Any], sources: List[dict]) -> str:
        normal, official_mcp, third_mcp = [], [], []
        for i, source in enumerate(sources, 1):
            block = (
                f"SOURCE {i}\n"
                f"FIELD: {source.get('field')}\n"
                f"TRACK: {source.get('mcp_track', 'n/a')}\n"
                f"TITLE: {source.get('title', '')}\n"
                f"URL: {source.get('url', '')}\n"
                f"SOURCE TYPE: {source.get('source_type', 'unknown')}\n"
                f"CONTENT:\n{source.get('content', '')}\n"
            )
            if source.get("field") == "mcp_official":
                official_mcp.append(block)
            elif source.get("field") == "mcp_third_party":
                third_mcp.append(block)
            else:
                normal.append(block)

        return f"""
You are the second-pass independent verifier for an auditable software
integration research dataset.

ORIGINAL RESEARCH
{self.safe_json(app)}

NORMAL INDEPENDENT EVIDENCE
{''.join(normal)}

OFFICIAL MCP CANDIDATES
{''.join(official_mcp) or 'No official-domain MCP candidates retrieved.'}

THIRD-PARTY MCP EVIDENCE
{''.join(third_mcp) or 'No third-party MCP evidence retrieved.'}

RULES
1. Re-evaluate every field independently. Do not agree merely because the
   original claim is plausible.
2. Use only the supplied independent evidence.
3. Lack of evidence is INSUFFICIENT, not contradiction.
4. CORRECTED/CONTRADICTED require concrete evidence for the replacement.
5. For arrays, verify individual items; unsupported items should be removed only
   when the evidence contradicts them.
6. api_pricing means API/developer cost only. SaaS subscription prices,
   transaction fees, quotas, and rate limits are not automatically API pricing.
7. api_access_model is about how API access relates to the account/plan and is
   separate from api_pricing.
8. self_serve_notes and api_surface must be checked for factual claims, not just
   whether their wording sounds reasonable.
9. credential_access and self_serve are related aliases. Keep them semantically
   aligned when correcting.
10. mcp_status and mcp_available are related aliases. Keep them aligned.
11. buildability_verdict and buildability are related aliases. Keep them aligned.
12. MCP MUST NEVER appear in api_type.
13. Do NOT label an API REST merely because it uses HTTP/JSON; REST must be
    explicitly supported by the supplied documentation.
14. Official MCP requires evidence that the company itself provides or maintains
   the implementation. A page on an official domain is a candidate, not proof by
   itself; the content must establish first-party ownership/maintenance.
15. Third-party MCP evidence alone can never justify official_mcp/official.
16. Do not turn normal setup requirements into a blocker unless they materially
   prevent practical integration.
17. If original API pricing is unknown and independent evidence remains
   inconclusive, use INSUFFICIENT.
18. Do not silently convert product/SaaS pricing into API pricing.
19. For every field listed in the REQUIRED CHECKLIST below, return exactly
    one VerificationCheck. Never omit a field because it is narrative or an
    alias. If evidence is insufficient, return INSUFFICIENT with an empty
    evidence_source_indexes array.
20. For narrative fields, verify the factual claims they contain. Do not require
    exact wording to mark a concise paraphrase SUPPORTED.
21. For `blocker`/`main_blocker`, a null value is SUPPORTED only when the
    evidence supports that there is no material integration blocker for the
    stated buildability verdict; otherwise mark it INSUFFICIENT or CORRECTED.
22. Use exact evidence_source_indexes from the supplied source list.
23. REQUIRED CHECKLIST (exactly one check each):
    description, one_liner, auth_methods, credential_access, self_serve,
    self_serve_notes, api_pricing, api_access_model, api_type, api_breadth,
    api_surface, mcp_status, mcp_available, buildability,
    buildability_verdict, buildability_notes, blocker, main_blocker.

ALLOWED VALUES
self_serve: free_self_serve, trial_self_serve, paid_plan_required,
admin_approval_required, partner_gated, contact_sales, unclear, not_applicable
mcp_status: official_mcp, community_mcp, none_found
buildability_verdict: ready_today, ready_with_workaround, blocked
credential_access: self_serve, trial, paid_required, admin_required,
partner_gated, contact_sales, unknown
api_pricing: free, freemium, paid, usage_based, unknown
api_access_model: included_in_plan, plan_dependent, separate_purchase,
usage_limited, free_access, unknown
api_breadth: broad, moderate, limited, none, unknown
mcp_available: official, third_party, not_found, unknown
buildability: buildable, buildable_with_constraints, blocked, unknown
api_type: explicit protocols/interfaces such as REST, GraphQL, SOAP, gRPC,
Webhooks, WebSocket, Pub/Sub; MCP NEVER belongs here.

Return ONLY VerificationResult.
"""

    def verify(self, app: AppResearch, website: Optional[str]) -> dict:
        original = app.model_dump() if isinstance(app, AppResearch) else dict(app)
        name = original.get("app", "")

        official_domains = get_official_domains(
            name,
            website,
        )
        print(f"[Verifier] {name}")
        sources = self.independent_search(original, website)

        if not sources:
            return {
                "app": name,
                "checks": [],
                "overall_verdict": "PASS_WITH_LIMITATIONS",
                "corrections_needed": False,
                "verification_notes": "No independent evidence retrieved.",
                "independent_sources": [],
                "mcp_official_sources": [],
                "mcp_third_party_sources": [],
            }

        prompt = self.build_prompt(original, sources)
        last = None
        for attempt in range(1, 4):
            try:
                result = self.structured_llm.invoke([HumanMessage(content=prompt)])
                raw = result.model_dump() if isinstance(result, VerificationResult) else VerificationResult.model_validate(result).model_dump()

                raw = sanitize_verification(raw, original)
                raw["independent_sources"] = sources
                raw["mcp_official_sources"] = [i for i, s in enumerate(sources, 1) if s.get("field") == "mcp_official"]
                raw["mcp_third_party_sources"] = [i for i, s in enumerate(sources, 1) if s.get("field") == "mcp_third_party"]
                raw = enforce_mcp_consistency(
                    raw,
                    sources,
                    official_domains,
                )

                # Require explicit verification coverage for every research field.
                # Missing checks are filled as INSUFFICIENT so the final dataset
                # never claims that an omitted field was independently verified.
                checks = raw.get("checks", []) or []
                sanitized_checks = []
                seen_fields = set()
                for check in checks:
                    field = check.get("field")
                    if field in seen_fields:
                        continue
                    seen_fields.add(field)
                    sanitized_checks.append(check)

                required_fields = [
                    "description", "one_liner", "auth_methods", "credential_access",
                    "self_serve", "self_serve_notes", "api_pricing",
                    "api_access_model", "api_type", "api_breadth", "api_surface",
                    "mcp_status", "mcp_available", "buildability",
                    "buildability_verdict", "buildability_notes", "blocker",
                    "main_blocker",
                ]
                checked = {c.get("field") for c in sanitized_checks}
                for field in required_fields:
                    if field not in checked:
                        sanitized_checks.append({
                            "field": field,
                            "original_value": original.get(field),
                            "verdict": "INSUFFICIENT",
                            "corrected_value": None,
                            "reasoning": "No explicit independent verification check was returned for this field.",
                            "evidence_source_indexes": [],
                        })

                # Re-run deterministic taxonomy sanitization after filling
                # omissions so every check is schema-safe.
                sanitized_checks = [
                    sanitize_verification({"checks": [c]} , original)["checks"][0]
                    for c in sanitized_checks
                ]
                raw["checks"] = sanitized_checks

                # Re-apply MCP provenance/alias rules after the complete check
                # list exists. This can correct a model downgrade when an
                # explicit first-party MCP page is present in the evidence.
                raw = enforce_mcp_consistency(raw, sources)
                checks = raw["checks"]

                # Deterministically synchronize the two MCP aliases from the
                # accepted provenance state. Never allow one alias to remain
                # "official" while the other is independently downgraded.
                proven_official = bool(
                    raw.get("mcp_official_sources")
                    and any(
                        _source_proves_first_party_mcp(
                            sources[i - 1],
                            raw.get("app", ""),
                            official_domains,
                        )
                        for i in raw.get("mcp_official_sources", [])
                        if 1 <= i <= len(sources)
                    )
                )
                if proven_official:
                    for check in checks:
                        if check.get("field") == "mcp_status":
                            if check.get("verdict") == "SUPPORTED" and check.get("original_value") == "official_mcp":
                                continue
                            check["verdict"] = "CORRECTED"
                            check["corrected_value"] = "official_mcp"
                            check["reasoning"] = (
                                (check.get("reasoning") or "")
                                + " Deterministic first-party MCP provenance confirms the vendor-provided implementation."
                            )
                        elif check.get("field") == "mcp_available":
                            if check.get("verdict") == "SUPPORTED" and check.get("original_value") == "official":
                                continue
                            check["verdict"] = "CORRECTED"
                            check["corrected_value"] = "official"
                            check["reasoning"] = (
                                (check.get("reasoning") or "")
                                + " Deterministic first-party MCP provenance confirms the official alias."
                            )

                # Normalize overall verdict from the actual sanitized checks; do
                # not trust a potentially stale LLM-generated verdict.
                has_correction = any(
                    c.get("verdict") in {"CORRECTED", "CONTRADICTED"}
                    for c in checks
                )
                has_uncertainty = any(
                    c.get("verdict") == "INSUFFICIENT" for c in checks
                )
                if has_correction and has_uncertainty:
                    raw["overall_verdict"] = "CORRECTED_WITH_LIMITATIONS"
                    raw["corrections_needed"] = True
                elif has_correction:
                    raw["overall_verdict"] = "CORRECTED"
                    raw["corrections_needed"] = True
                elif has_uncertainty:
                    raw["overall_verdict"] = "PASS_WITH_LIMITATIONS"
                    raw["corrections_needed"] = False
                else:
                    raw["overall_verdict"] = "PASS"
                    raw["corrections_needed"] = False

                verified_count = sum(
                    c.get("verdict") in {"SUPPORTED", "CORRECTED", "CONTRADICTED"}
                    for c in checks
                )
                unresolved_count = sum(c.get("verdict") == "INSUFFICIENT" for c in checks)
                corrected_count = sum(
                    c.get("verdict") in {"CORRECTED", "CONTRADICTED"}
                    for c in checks
                )
                raw["verification_notes"] = (
                    f"Independently checked {verified_count}/{len(required_fields)} fields; "
                    f"{unresolved_count} remain insufficient and {corrected_count} were corrected/contradicted. "
                    "Narrative fields were checked for factual support, and MCP/API protocol "
                    "classifications were subject to deterministic provenance guardrails."
                )

                VerificationResult.model_validate(raw)
                return raw
            except Exception as exc:
                last = exc
                print(f"[Verifier] Attempt {attempt}/3 failed for {name}: {exc}")
                if any(x in str(exc).lower() for x in ("quota", "429", "resource exhausted", "rate limit")):
                    break
                time.sleep(2 * attempt)

        return {
            "app": name,
            "checks": [],
            "overall_verdict": "PASS_WITH_LIMITATIONS",
            "corrections_needed": False,
            "verification_notes": f"Verification failed: {last}",
            "independent_sources": sources,
            "mcp_official_sources": [i for i, s in enumerate(sources, 1) if s.get("field") == "mcp_official"],
            "mcp_third_party_sources": [i for i, s in enumerate(sources, 1) if s.get("field") == "mcp_third_party"],
        }
