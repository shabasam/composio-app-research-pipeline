import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse


OFFICIAL_DOMAIN_ALIASES = {
    "shopify": {"shopify.com", "shopify.dev"},
    "hubspot": {"hubspot.com", "developers.hubspot.com"},
    "salesforce": {
        "salesforce.com",
        "developer.salesforce.com",
        "trailhead.salesforce.com",
    },
    "slack": {"slack.com", "api.slack.com"},
    "stripe": {"stripe.com", "docs.stripe.com"},
}


SCALAR_ALLOWED = {
    "self_serve": {
        "free_self_serve",
        "trial_self_serve",
        "paid_plan_required",
        "admin_approval_required",
        "partner_gated",
        "contact_sales",
        "unclear",
        "not_applicable",
    },
    "mcp_status": {
        "official_mcp",
        "community_mcp",
        "none_found",
    },
    "buildability_verdict": {
        "ready_today",
        "ready_with_workaround",
        "blocked",
    },
    "credential_access": {
        "self_serve",
        "trial",
        "paid_required",
        "admin_required",
        "partner_gated",
        "contact_sales",
        "unknown",
    },
    "api_pricing": {
        "free",
        "freemium",
        "paid",
        "usage_based",
        "unknown",
    },
    "api_access_model": {
        "included_in_plan",
        "plan_dependent",
        "separate_purchase",
        "usage_limited",
        "free_access",
        "unknown",
    },
    "api_breadth": {
        "broad",
        "moderate",
        "limited",
        "none",
        "unknown",
    },
    "mcp_available": {
        "official",
        "third_party",
        "not_found",
        "unknown",
    },
    "buildability": {
        "buildable",
        "buildable_with_constraints",
        "blocked",
        "unknown",
    },
}


ARRAY_FIELDS = {
    "auth_methods",
    "api_type",
    "evidence_urls",
}


NARRATIVE_FIELDS = {
    "one_liner",
    "self_serve_notes",
    "api_surface",
    "buildability_notes",
}


IMPORTANT_FIELDS = [
    "auth_methods",
    "credential_access",
    "self_serve",
    "self_serve_notes",
    "api_pricing",
    "api_access_model",
    "api_type",
    "api_breadth",
    "mcp_available",
    "mcp_status",
    "buildability",
    "buildability_verdict",
    "api_surface",
    "buildability_notes",
    "blocker",
    "main_blocker",
]


VERIFIABLE_FIELDS = ["description", "one_liner"] + IMPORTANT_FIELDS


FIELD_WEIGHTS = {
    "auth_methods": 1.1,
    "credential_access": 1.0,
    "self_serve": 1.0,
    "self_serve_notes": 0.7,
    "api_pricing": 1.0,
    "api_access_model": 1.0,
    "api_type": 1.0,
    "api_breadth": 1.0,
    "mcp_available": 1.1,
    "mcp_status": 1.1,
    "buildability": 1.0,
    "buildability_verdict": 1.0,
    "api_surface": 0.9,
    "buildability_notes": 0.7,
    "blocker": 0.5,
    "main_blocker": 0.5,
}


VERIFICATION_SCORE = {
    "VERIFIED": 1.0,
    "CORRECTED": 0.78,
    "UNVERIFIED": 0.55,
    "CONTRADICTED": 0.20,
}


def normalize_domain(value: str) -> str:
    if not value:
        return ""

    value = value.strip().lower()

    if "://" in value:
        value = urlparse(value).netloc

    value = value.split("/")[0].split("?")[0].split("#")[0]

    return value.removeprefix("www.")


def get_official_domains(
    app_name: str,
    website: Optional[str] = None,
) -> list[str]:
    """
    Return known first-party domains for an app.

    This combines:
      1. hard-coded official aliases
      2. the app's website domain, when provided

    Keeping website-derived domains here is important because many vendors
    expose developer/docs/API content on a domain different from their
    marketing website.
    """
    key = (app_name or "").strip().lower()

    domains = set(
        OFFICIAL_DOMAIN_ALIASES.get(key, set())
    )

    website_domain = normalize_domain(website or "")

    if website_domain:
        domains.add(website_domain)

    return sorted(domains)


def is_official_url(
    url: str,
    domain: Optional[Any],
) -> bool:
    """
    Return True when the URL host is exactly one of the supplied domains
    or a subdomain of one of them.
    """
    host = normalize_domain(url)

    if not host or not domain:
        return False

    if isinstance(domain, (list, tuple, set)):
        domains = [
            normalize_domain(str(x))
            for x in domain
            if x
        ]
    else:
        domains = [
            normalize_domain(str(domain))
        ]

    return any(
        host == d or host.endswith("." + d)
        for d in domains
        if d
    )


def classify_source(
    url: str,
    domain: Optional[Any],
) -> str:
    """
    Classify a source based on whether it belongs to a first-party domain
    and, if so, what kind of first-party source it appears to be.
    """
    if not is_official_url(url, domain):
        return "third_party"

    low = url.lower()
    path = urlparse(url).path.lower()

    if "github.com" in low and is_official_url(url, domain):
        return "official_github"

    if "pricing" in low:
        return "official_pricing"

    if "community" in low or "forum" in low:
        return "official_community"

    if any(
        x in path
        for x in (
            "/marketplace/",
            "/app-store/",
            "/appstore/",
        )
    ):
        return "official_marketplace"

    if any(
        x in low
        for x in (
            "developer",
            "developers",
            "docs",
            "api",
            "reference",
        )
    ):
        return "official_developer"

    return "official_docs"


def load_json(path: str):
    if not os.path.exists(path):
        return []

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def save_json(
    path: str,
    data: Any,
):
    Path(path).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


def find_by_app(
    records: Iterable[dict],
    app: str,
) -> Optional[dict]:
    for record in records:
        if record.get("app") == app:
            return record

    return None


def unique_preserve(items):
    seen = set()
    out = []

    for item in items:
        key = json.dumps(
            item,
            sort_keys=True,
            default=str,
        )

        if key not in seen:
            out.append(item)
            seen.add(key)

    return out


def valid_value(
    field: str,
    value: Any,
) -> bool:
    if field in ARRAY_FIELDS:
        return (
            isinstance(value, list)
            and all(
                isinstance(x, str)
                for x in value
            )
        )

    if field in SCALAR_ALLOWED:
        return (
            isinstance(value, str)
            and value in SCALAR_ALLOWED[field]
        )

    if field in NARRATIVE_FIELDS:
        return isinstance(value, str)

    if field in {
        "main_blocker",
        "blocker",
    }:
        return (
            value is None
            or isinstance(value, str)
        )

    return True


def confidence_label(
    score: float,
) -> str:
    """
    Return the label implied by the canonical numeric confidence score.
    """
    score = float(score)

    if score >= 0.90:
        return "high"

    if score >= 0.75:
        return "medium"

    return "low"


def _canonical_aliases(
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Keep the rich human-readable fields and legacy normalized fields aligned.
    """
    out = dict(result)

    self_map = {
        "free_self_serve": "self_serve",
        "trial_self_serve": "trial",
        "paid_plan_required": "paid_required",
        "admin_approval_required": "admin_required",
        "partner_gated": "partner_gated",
        "contact_sales": "contact_sales",
        "unclear": "unknown",
        "not_applicable": "unknown",
    }

    mcp_map = {
        "official_mcp": "official",
        "community_mcp": "third_party",
        "none_found": "not_found",
    }

    build_map = {
        "ready_today": "buildable",
        "ready_with_workaround": "buildable_with_constraints",
        "blocked": "blocked",
    }

    if out.get("self_serve") in self_map:
        out["credential_access"] = self_map[
            out["self_serve"]
        ]

    if out.get("mcp_status") in mcp_map:
        out["mcp_available"] = mcp_map[
            out["mcp_status"]
        ]

    if out.get("buildability_verdict") in build_map:
        out["buildability"] = build_map[
            out["buildability_verdict"]
        ]

    if (
        "blocker" in out
        and out.get("main_blocker") is None
    ):
        out["main_blocker"] = out.get("blocker")

    elif (
        "main_blocker" in out
        and out.get("blocker") is None
    ):
        out["blocker"] = out.get("main_blocker")

    confidence = out.get("confidence")

    if isinstance(confidence, (int, float)):
        out["confidence_label"] = confidence_label(
            confidence
        )

    return out


def sanitize_check(
    check: Dict[str, Any],
    actual_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic post-LLM guardrail.

    LLMs may emit semantic values that are not part of our taxonomy
    (e.g. "free_with_limitations", or "plan_dependent" under
    credential_access).

    Never allow such values into the final dataset.
    """
    field = check.get("field")
    out = dict(check or {})

    actual = actual_result.get(field)

    if field not in VERIFIABLE_FIELDS:
        out["verdict"] = "INSUFFICIENT"
        out["corrected_value"] = None
        out["reasoning"] = (
            f"Ignored unknown verification field "
            f"'{field}'."
        )
        return out

    # Trust the actual research record as the canonical original value.
    out["original_value"] = actual

    verdict = out.get("verdict")
    corrected = out.get("corrected_value")

    if verdict in {
        "CORRECTED",
        "CONTRADICTED",
    }:
        if not valid_value(
            field,
            corrected,
        ):
            out["verdict"] = "INSUFFICIENT"
            out["corrected_value"] = None
            out["reasoning"] = (
                (out.get("reasoning") or "")
                + " Verifier returned a value outside "
                  "the schema taxonomy; the correction "
                  "was rejected and kept unresolved."
            )

    # An original "unknown" value is only SUPPORTED when
    # the verifier has supplied concrete independent
    # evidence that the field is genuinely unresolved.
    #
    # Otherwise, leave it UNVERIFIED rather than treating
    # lack of evidence as confirmation.
    if verdict == "SUPPORTED" and actual == "unknown":
        evidence_indexes = (
            out.get("evidence_source_indexes")
            or []
        )

        if not evidence_indexes:
            out["verdict"] = "INSUFFICIENT"
            out["corrected_value"] = None
            out["reasoning"] = (
                (out.get("reasoning") or "")
                + " Original value is unknown and no "
                  "independent evidence was supplied to "
                  "support that unknown classification."
            )

    # SUPPORTED claims with invalid originals are impossible;
    # downgrade.
    if (
        verdict == "SUPPORTED"
        and not valid_value(field, actual)
    ):
        out["verdict"] = "INSUFFICIENT"
        out["corrected_value"] = None
        out["reasoning"] = (
            (out.get("reasoning") or "")
            + " Original value is not schema-valid."
        )

    return out


def _source_proves_first_party_mcp(
    source: Dict[str, Any],
    app_name: str = "",
    official_domains: Optional[Any] = None,
) -> bool:
    """
    Return True when the supplied source is strong enough to establish that
    the vendor itself provides/maintains the MCP implementation.

    Provenance rule:
      * source must be retrieved from a known first-party domain;
      * source must discuss MCP / Model Context Protocol;
      * content must contain a first-party ownership signal OR explicitly
        name the vendor and its MCP server.

    A third-party source can corroborate official ownership but can never
    prove it by itself.
    """
    url = str(
        source.get("url") or ""
    )

    source_type = str(
        source.get("source_type") or ""
    )

    field = source.get("field")
    track = source.get("mcp_track")

    if (
        field != "mcp_official"
        or track != "official"
    ):
        return False

    if not is_official_url(
        url,
        official_domains,
    ):
        return False

    title = " ".join(
        str(source.get("title") or "")
        .lower()
        .split()
    )

    content = " ".join(
        str(source.get("content") or "")
        .lower()
        .split()
    )

    supports = " ".join(
        str(source.get("supports") or "")
        .lower()
        .split()
    )

    combined = " ".join(
        x
        for x in (
            title,
            content,
            supports,
        )
        if x
    )

    if not combined:
        return False

    if (
        "mcp" not in combined
        and "model context protocol" not in combined
    ):
        return False

    brand = (
        (app_name or "")
        .strip()
        .lower()
    )

    # Vendor names with spaces/punctuation are normalized for a slightly more
    # forgiving first-party ownership check.
    brand_tokens = [
        token
        for token in re.split(
            r"[^a-z0-9]+",
            brand,
        )
        if len(token) >= 3
    ]

    ownership_signals = (
        "official mcp",
        "official model context protocol",
        "our mcp",
        "our model context protocol",
        "officially supported mcp",
        "official mcp server",
        "we provide mcp",
        "we provide an mcp",
        "we offer mcp",
        "we offer an mcp",
        "provides an official mcp",
        "provides a mcp server",
        "provides official mcp",
        "provides official storefront mcp",
        "official implementation",
        "official server",
        "mcp support",
        "mcp server",
    )

    explicit_vendor_mcp = (
        bool(brand_tokens)
        and all(
            token in combined
            for token in brand_tokens
        )
        and (
            "mcp server" in combined
            or "mcp support" in combined
            or "model context protocol" in combined
        )
    )

    # Only first-party source types can establish ownership.
    # In particular, npm/GitHub/community pages classified
    # as third_party never qualify.
    first_party_types = {
        "official_docs",
        "official_developer",
        "official_pricing",
        "official_marketplace",
        "official_github",
        "official_community",
    }

    if (
        source_type
        and source_type not in first_party_types
    ):
        return False

    return (
        explicit_vendor_mcp
        or any(
            signal in combined
            for signal in ownership_signals
        )
    )


def enforce_mcp_consistency(
    raw: Dict[str, Any],
    sources: list[dict],
    official_domains: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Apply deterministic MCP provenance and alias consistency rules.

    IMPORTANT:
    The verifier already resolves official domains using both:
      - OFFICIAL_DOMAIN_ALIASES
      - the app's website

    Passing those resolved domains into this function prevents the verifier
    from losing website-derived first-party provenance.
    """
    out = dict(raw)

    checks = list(
        out.get("checks", [])
        or []
    )

    official_indexes = {
        i
        for i, source in enumerate(sources, 1)
        if source.get("field") == "mcp_official"
    }

    third_party_indexes = {
        i
        for i, source in enumerate(sources, 1)
        if source.get("field") == "mcp_third_party"
    }

    app_name = str(
        out.get("app") or ""
    )

    # FIX:
    # Prefer the already-resolved domains supplied by verifier.py.
    # Fall back to aliases only when the caller did not supply them.
    if official_domains is None:
        official_domains = get_official_domains(
            app_name
        )

    proven_official_indexes = {
        i
        for i, source in enumerate(sources, 1)
        if _source_proves_first_party_mcp(
            source,
            app_name,
            official_domains,
        )
    }

    status_to_availability = {
        "official_mcp": "official",
        "community_mcp": "third_party",
        "none_found": "not_found",
    }

    availability_to_status = {
        value: key
        for key, value in status_to_availability.items()
    }

    def normalize(
        check: dict,
    ) -> tuple[Any, Any]:
        field = check.get("field")

        original = check.get(
            "original_value"
        )

        corrected = check.get(
            "corrected_value"
        )

        if field == "mcp_status":
            return (
                status_to_availability.get(
                    original,
                    original,
                ),
                status_to_availability.get(
                    corrected,
                    corrected,
                ),
            )

        return (
            original,
            corrected,
        )

    # First, use the model's evidence citations where present.
    # Then use the retrieved first-party content itself as a deterministic
    # final guardrail so a model cannot incorrectly downgrade an explicit
    # official MCP page to a community implementation.
    for check in checks:
        if check.get("field") not in {
            "mcp_status",
            "mcp_available",
        }:
            continue

        original, corrected = normalize(
            check
        )

        verdict = check.get("verdict")

        evidence_indexes = set(
            check.get(
                "evidence_source_indexes"
            )
            or []
        )

        cited_proven_official = bool(
            evidence_indexes
            & proven_official_indexes
        )

        cited_official_track = bool(
            evidence_indexes
            & official_indexes
        )

        cited_third_party = bool(
            evidence_indexes
            & third_party_indexes
        )

        # Explicit first-party evidence wins over a community classification.
        # This fixes cases where the LLM notices a GitHub community server but
        # overlooks an official vendor MCP page also present in the evidence.
        if (
            proven_official_indexes
            and (
                corrected in {
                    "third_party",
                    "not_found",
                }
                or (
                    verdict == "SUPPORTED"
                    and original in {
                        "third_party",
                        "not_found",
                    }
                )
            )
        ):
            check["verdict"] = "CORRECTED"

            check["corrected_value"] = (
                "official_mcp"
                if check.get("field")
                == "mcp_status"
                else "official"
            )

            check["evidence_source_indexes"] = sorted(
                set(
                    check.get(
                        "evidence_source_indexes"
                    )
                    or []
                )
                | proven_official_indexes
            )

            check["reasoning"] = (
                (check.get("reasoning") or "")
                + " Deterministic provenance correction: "
                  "first-party evidence explicitly documents "
                  "an official MCP implementation."
            )

            continue

        # Official claims must cite a proven first-party source,
        # not merely a URL on the vendor domain.
        if (
            original == "official"
            and not cited_proven_official
        ):
            check["verdict"] = "INSUFFICIENT"
            check["corrected_value"] = None

            check["reasoning"] = (
                (check.get("reasoning") or "")
                + " Official MCP classification is unresolved "
                  "because no cited source explicitly establishes "
                  "first-party MCP ownership."
            )

            continue

        if (
            corrected == "official"
            and not cited_proven_official
        ):
            check["verdict"] = "INSUFFICIENT"
            check["corrected_value"] = None

            check["reasoning"] = (
                (check.get("reasoning") or "")
                + " Official MCP correction rejected because "
                  "the cited evidence does not establish "
                  "first-party ownership."
            )

            continue

        # If only community evidence supports a third-party classification
        # and official evidence exists but was not ruled out, remain conservative.
        if (
            corrected == "third_party"
            and cited_third_party
            and official_indexes
            and not cited_official_track
        ):
            check["verdict"] = "INSUFFICIENT"
            check["corrected_value"] = None

            check["reasoning"] = (
                (check.get("reasoning") or "")
                + " Third-party MCP classification left unresolved "
                  "because official MCP evidence was also retrieved "
                  "and was not independently ruled out."
            )

    # Derive a single accepted MCP value after the above provenance checks.
    accepted = None

    priority = {
        "official": 3,
        "third_party": 2,
        "not_found": 1,
    }

    candidates = []

    for check in checks:
        if check.get("field") not in {
            "mcp_status",
            "mcp_available",
        }:
            continue

        if check.get("verdict") not in {
            "SUPPORTED",
            "CORRECTED",
            "CONTRADICTED",
        }:
            continue

        original, corrected = normalize(
            check
        )

        value = (
            corrected
            if corrected is not None
            else original
        )

        if value in priority:
            candidates.append(value)

    if proven_official_indexes:
        accepted = "official"

    elif candidates:
        accepted = max(
            candidates,
            key=lambda x: priority[x],
        )

    if accepted in availability_to_status:
        target_status = availability_to_status[
            accepted
        ]

        for check in checks:
            field = check.get("field")

            if (
                field == "mcp_status"
                and check.get("verdict")
                in {
                    "SUPPORTED",
                    "CORRECTED",
                    "CONTRADICTED",
                }
            ):
                if check.get("verdict") == "SUPPORTED":
                    check["original_value"] = target_status

            elif (
                field == "mcp_available"
                and check.get("verdict")
                in {
                    "SUPPORTED",
                    "CORRECTED",
                    "CONTRADICTED",
                }
            ):
                if check.get("verdict") == "SUPPORTED":
                    check["original_value"] = accepted

    out["checks"] = checks

    return out


def sanitize_verification(
    raw: Dict[str, Any],
    actual_result: Dict[str, Any],
) -> Dict[str, Any]:
    checks = []
    seen_fields = set()

    for raw_check in (
        raw.get("checks", [])
        or []
    ):
        field = raw_check.get("field")

        if field in seen_fields:
            continue

        seen_fields.add(field)

        checks.append(
            sanitize_check(
                raw_check,
                actual_result,
            )
        )

    raw = dict(raw)
    raw["checks"] = checks

    return raw


def recalculate_confidence(
    result: Dict[str, Any],
    checks: list[dict],
) -> float:
    # Start every important field as UNVERIFIED so omitted checks cannot
    # accidentally inflate confidence.
    statuses = {
        field: "UNVERIFIED"
        for field in IMPORTANT_FIELDS
    }

    for check in checks:
        field = check.get("field")

        if field not in IMPORTANT_FIELDS:
            continue

        verdict = check.get("verdict")

        statuses[field] = {
            "SUPPORTED": "VERIFIED",
            "CORRECTED": "CORRECTED",
            "INSUFFICIENT": "UNVERIFIED",
            "CONTRADICTED": "CONTRADICTED",
        }.get(
            verdict,
            "UNVERIFIED",
        )

    weighted = 0.0
    total = 0.0

    for field, weight in FIELD_WEIGHTS.items():
        status = statuses.get(
            field,
            "UNVERIFIED",
        )

        weighted += (
            VERIFICATION_SCORE[status]
            * weight
        )

        total += weight

    score = (
        weighted / total
        if total
        else 0.0
    )

    unverified = sum(
        v == "UNVERIFIED"
        for v in statuses.values()
    )

    corrected = sum(
        v == "CORRECTED"
        for v in statuses.values()
    )

    contradicted = sum(
        v == "CONTRADICTED"
        for v in statuses.values()
    )

    # Confidence must reflect unresolved verification coverage.
    # A record with even one important field still unverified should not
    # receive a near-certain confidence score.
    if unverified >= 1:
        score = min(
            score,
            0.95,
        )

    if unverified >= 2:
        score = min(
            score,
            0.90,
        )

    if unverified >= 3:
        score = min(
            score,
            0.84,
        )

    if unverified >= 5:
        score = min(
            score,
            0.76,
        )

    if unverified >= 7:
        score = min(
            score,
            0.68,
        )

    if corrected:
        score = min(
            score,
            0.94,
        )

    if corrected >= 2:
        score = min(
            score,
            0.92,
        )

    if contradicted:
        score = min(
            score,
            0.85,
        )

    return round(
        max(
            0.0,
            min(
                1.0,
                score,
            ),
        ),
        2,
    )


def apply_verification(
    research_result: "AppResearch",
    verification: dict,
    scope: str = "sample",
):
    from src.models import AppResearch

    final = research_result.model_copy(
        deep=True
    )

    actual = final.model_dump()

    statuses = {
        field: "UNVERIFIED"
        for field in IMPORTANT_FIELDS
    }

    corrections = []
    uncertainties = []

    for field in [
        "description",
        "one_liner",
    ] + IMPORTANT_FIELDS:
        statuses[field] = "UNVERIFIED"

    checks = (
        verification.get("checks", [])
        or []
    )

    for check in checks:
        field = check.get("field")
        verdict = check.get("verdict")
        corrected = check.get("corrected_value")
        reasoning = check.get(
            "reasoning",
            "",
        )

        if field not in actual:
            continue

        if verdict == "SUPPORTED":
            statuses[field] = "VERIFIED"

        elif (
            verdict == "CORRECTED"
            and valid_value(
                field,
                corrected,
            )
        ):
            setattr(
                final,
                field,
                corrected,
            )

            statuses[field] = "CORRECTED"

            corrections.append(
                {
                    "field": field,
                    "from": actual.get(field),
                    "to": corrected,
                    "reasoning": reasoning,
                }
            )

        elif (
            verdict == "CONTRADICTED"
            and valid_value(
                field,
                corrected,
            )
        ):
            setattr(
                final,
                field,
                corrected,
            )

            statuses[field] = "CONTRADICTED"

            corrections.append(
                {
                    "field": field,
                    "from": actual.get(field),
                    "to": corrected,
                    "type": "contradicted",
                    "reasoning": reasoning,
                }
            )

        else:
            statuses[field] = "UNVERIFIED"

            uncertainties.append(
                {
                    "field": field,
                    "original_value": actual.get(field),
                    "reasoning": reasoning,
                }
            )

    # Reconcile rich fields with legacy normalized aliases after verification.
    synced = _canonical_aliases(
        final.model_dump()
    )

    final = AppResearch.model_validate(
        synced
    )

    # Explicitly make evidence-aware statuses for all important fields.
    final.field_verification = statuses
    final.verification_scope = scope

    final.confidence = recalculate_confidence(
        final.model_dump(),
        checks,
    )

    final.confidence_label = confidence_label(
        final.confidence
    )

    final = AppResearch.model_validate(
        final.model_dump()
    )

    return (
        final,
        corrections,
        uncertainties,
    )