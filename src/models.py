from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict


CredentialAccess = Literal[
    "self_serve", "trial", "paid_required", "admin_required",
    "partner_gated", "contact_sales", "unknown"
]
SelfServe = Literal[
    "free_self_serve", "trial_self_serve", "paid_plan_required",
    "admin_approval_required", "partner_gated", "contact_sales",
    "unclear", "not_applicable"
]
McpStatus = Literal["official_mcp", "community_mcp", "none_found"]
BuildabilityVerdict = Literal["ready_today", "ready_with_workaround", "blocked"]
ApiPricing = Literal["free", "freemium", "paid", "usage_based", "unknown"]
ApiAccessModel = Literal[
    "included_in_plan", "plan_dependent", "separate_purchase",
    "usage_limited", "free_access", "unknown"
]
ApiBreadth = Literal["broad", "moderate", "limited", "none", "unknown"]
McpAvailability = Literal["official", "third_party", "not_found", "unknown"]
Buildability = Literal["buildable", "buildable_with_constraints", "blocked", "unknown"]
SourceType = Literal[
    "official_docs", "official_developer", "official_pricing",
    "official_marketplace", "official_github", "official_community",
    "third_party", "community", "unknown"
]
VerificationStatus = Literal[
    "VERIFIED", "CORRECTED", "UNVERIFIED", "CONTRADICTED"
]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: Optional[str] = None
    source_type: SourceType
    field: str
    supports: str


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: int = Field(ge=1)
    field: str
    supports: str


class ResearchExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str
    category: str
    description: str
    one_liner: str
    auth_methods: List[str]
    self_serve: SelfServe
    self_serve_notes: str
    api_surface: str
    credential_access: CredentialAccess
    api_pricing: ApiPricing
    api_access_model: ApiAccessModel
    api_type: List[str]
    api_breadth: ApiBreadth
    mcp_available: McpAvailability
    mcp_status: McpStatus
    buildability: Buildability
    buildability_verdict: BuildabilityVerdict
    buildability_notes: Optional[str] = None
    blocker: Optional[str] = None
    main_blocker: Optional[str] = None
    evidence_refs: List[EvidenceRef] = []
    confidence: float = Field(ge=0, le=1)
    research_notes: Optional[str] = None


class AppResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str
    category: str
    description: str
    one_liner: str
    auth_methods: List[str]
    self_serve: SelfServe
    self_serve_notes: str
    api_surface: str
    credential_access: CredentialAccess
    api_pricing: ApiPricing
    api_access_model: ApiAccessModel
    api_type: List[str]
    api_breadth: ApiBreadth
    mcp_available: McpAvailability
    mcp_status: McpStatus
    buildability: Buildability
    buildability_verdict: BuildabilityVerdict
    buildability_notes: Optional[str] = None
    blocker: Optional[str] = None
    main_blocker: Optional[str] = None
    evidence: List[Evidence] = []
    evidence_urls: List[str] = []
    confidence: float = Field(ge=0, le=1)
    confidence_label: Literal["high", "medium", "low"] = "low"
    research_notes: Optional[str] = None
    field_verification: Dict[str, VerificationStatus] = {}
    verification_scope: Literal["not_verified", "sample"] = "not_verified"


class VerificationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    original_value: Any
    verdict: Literal[
        "SUPPORTED", "CORRECTED", "INSUFFICIENT", "CONTRADICTED"
    ]
    corrected_value: Optional[Any] = None
    reasoning: str
    evidence_source_indexes: List[int] = []


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    app: str
    checks: List[VerificationCheck] = []
    overall_verdict: Literal[
        "PASS", "PASS_WITH_LIMITATIONS", "CORRECTED", "CORRECTED_WITH_LIMITATIONS", "FAIL"
    ]
    corrections_needed: bool
    verification_notes: Optional[str] = None
    independent_sources: List[dict] = []
    mcp_official_sources: List[int] = []
    mcp_third_party_sources: List[int] = []
