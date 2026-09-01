"""Dataset-free candidate/kit compatibility policy."""

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from core.mapping.contracts import CandidateKitCompatibilityResult


CORE_ROLE_COVERAGE_INCOMPLETE = "CORE_ROLE_COVERAGE_INCOMPLETE"
UNKNOWN_EVENT_RATIO_EXCEEDED = "UNKNOWN_EVENT_RATIO_EXCEEDED"
MAPPING_CONFIDENCE_TOO_LOW = "MAPPING_CONFIDENCE_TOO_LOW"
MAPPING_COLLISION = "MAPPING_COLLISION"
CANDIDATE_KIT_INCOMPATIBLE = "CANDIDATE_KIT_INCOMPATIBLE"
DUPLICATE_COLLISION = "DUPLICATE_COLLISION"


CORE_ROLES = frozenset({"kick", "snare", "closed_hat"})


def _coverage_role(role: str) -> str:
    if role in {"snare", "clap", "rim", "snare_roll"}:
        return "snare"
    if role in {"closed_hat", "hat"}:
        return "closed_hat"
    return role


@dataclass(frozen=True)
class CandidateKitCompatibilityPolicy:
    maximum_unknown_event_ratio: float = 0.10
    minimum_mapping_confidence: float = 0.85
    schema_version: str = "candidate-kit-compatibility-policy.v1"

    def evaluate(
        self,
        *,
        source_roles: Iterable[str],
        target_roles: Iterable[str],
        source_event_count: int,
        unknown_event_count: int,
        mapped_event_count: int,
        fallback_event_count: int,
        mapping_collisions=(),
        physical_duplicates=(),
    ) -> CandidateKitCompatibilityResult:
        source = {_coverage_role(str(role)) for role in source_roles}
        target = {_coverage_role(str(role)) for role in target_roles}
        missing_source = sorted(CORE_ROLES - source)
        missing_target = sorted(CORE_ROLES - target)
        core_coverage = (len(CORE_ROLES) - len(missing_source)) / len(CORE_ROLES)
        all_coverage = len(source & target) / len(source) if source else 0.0
        unknown_ratio = unknown_event_count / source_event_count if source_event_count else 0.0
        fallback_ratio = fallback_event_count / source_event_count if source_event_count else 0.0
        confidence = max(0.0, min(1.0, core_coverage * (1.0 - unknown_ratio) * (1.0 - fallback_ratio)))
        rejection = []
        if missing_source or missing_target:
            rejection.append(CORE_ROLE_COVERAGE_INCOMPLETE)
        if unknown_ratio > self.maximum_unknown_event_ratio:
            rejection.append(UNKNOWN_EVENT_RATIO_EXCEEDED)
        if confidence < self.minimum_mapping_confidence:
            rejection.append(MAPPING_CONFIDENCE_TOO_LOW)
        if mapping_collisions:
            rejection.append(MAPPING_COLLISION)
        if rejection:
            rejection.append(CANDIDATE_KIT_INCOMPATIBLE)
        warnings = []
        if physical_duplicates:
            warnings.append(DUPLICATE_COLLISION)
        return CandidateKitCompatibilityResult(
            compatible=not rejection,
            core_role_coverage=round(core_coverage, 6),
            all_role_coverage=round(all_coverage, 6),
            unknown_event_ratio=round(unknown_ratio, 6),
            fallback_event_ratio=round(fallback_ratio, 6),
            mapping_confidence=round(confidence, 6),
            missing_source_roles=tuple(missing_source),
            missing_target_roles=tuple(missing_target),
            mapping_collisions=tuple(dict(item) for item in mapping_collisions),
            physical_duplicates=tuple(dict(item) for item in physical_duplicates),
            rejection_codes=tuple(dict.fromkeys(rejection)),
            warnings=tuple(warnings),
        )
