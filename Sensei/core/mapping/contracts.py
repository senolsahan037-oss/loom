"""Versioned mapping observability contracts."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SourceMappingTrace:
    source_reference: Optional[str]
    source_note: Optional[int]
    source_position: float
    source_duration: float
    source_velocity: int
    resolved_role: Optional[str]
    role_evidence: str
    role_confidence: float
    target_role: Optional[str]
    target_note: Optional[int]
    target_pad_name: Optional[str]
    target_chain_name: Optional[str]
    target_choke_group: Optional[int]
    mapping_policy: str
    mapping_confidence: float
    fallback_used: bool
    fallback_reason: Optional[str]
    status: str
    trace_id: Optional[str] = None
    schema_version: str = "source-mapping-trace.v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateKitCompatibilityResult:
    compatible: bool
    core_role_coverage: float
    all_role_coverage: float
    unknown_event_ratio: float
    fallback_event_ratio: float
    mapping_confidence: float
    missing_source_roles: tuple[str, ...] = ()
    missing_target_roles: tuple[str, ...] = ()
    mapping_collisions: tuple[Dict[str, Any], ...] = ()
    physical_duplicates: tuple[Dict[str, Any], ...] = ()
    rejection_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = "candidate-kit-compatibility-result.v1"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        for key in ("missing_source_roles", "missing_target_roles", "rejection_codes", "warnings"):
            value[key] = list(value[key])
        value["mapping_collisions"] = [dict(item) for item in self.mapping_collisions]
        value["physical_duplicates"] = [dict(item) for item in self.physical_duplicates]
        return value


@dataclass(frozen=True)
class MappingInvariantReport:
    source_event_count: int
    mapped_event_count: int
    skipped_event_count: int
    unknown_event_count: int
    fallback_event_count: int
    physical_event_count: int
    role_distribution: Dict[str, int] = field(default_factory=dict)
    evidence_distribution: Dict[str, int] = field(default_factory=dict)
    target_note_distribution: Dict[str, int] = field(default_factory=dict)
    core_role_coverage: float = 0.0
    mapping_confidence: float = 0.0
    mapping_collisions: tuple[Dict[str, Any], ...] = ()
    physical_duplicates: tuple[Dict[str, Any], ...] = ()
    choke_collisions: tuple[Dict[str, Any], ...] = ()
    unmapped_roles: tuple[str, ...] = ()
    source_note_leaks: tuple[Dict[str, Any], ...] = ()
    schema_version: str = "mapping-invariant-report.v1"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        for key in ("mapping_collisions", "physical_duplicates", "choke_collisions", "source_note_leaks"):
            value[key] = [dict(item) for item in value[key]]
        value["unmapped_roles"] = list(value["unmapped_roles"])
        return value
