"""Build source-to-target traces before the writer boundary."""

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable

from core.mapping.contracts import MappingInvariantReport, SourceMappingTrace


def _valid_layering(events):
    by_position = defaultdict(list)
    for event in events:
        by_position[round(float(event["beat"]), 6)].append(event)
    return [
        {"position": position, "roles": sorted({str(item["role"]) for item in items}), "notes": sorted({int(item["note"]) for item in items})}
        for position, items in sorted(by_position.items())
        if len({str(item["role"]) for item in items}) > 1 and len({int(item["note"]) for item in items}) > 1
    ]


def _choke_collisions(role_to_note, target_pad_map):
    closed_note = role_to_note.get("closed_hat") or role_to_note.get("hat")
    open_note = role_to_note.get("open_hat")
    if closed_note is None or open_note is None:
        return []
    closed = target_pad_map.get(str(closed_note), {})
    opened = target_pad_map.get(str(open_note), {})
    same_note = int(closed_note) == int(open_note)
    closed_choke, open_choke = closed.get("choke_group"), opened.get("choke_group")
    if same_note or closed_choke is None or open_choke is None or closed_choke != open_choke:
        return [{
            "closed_hat_note": int(closed_note), "open_hat_note": int(open_note),
            "closed_hat_choke_group": closed_choke, "open_hat_choke_group": open_choke,
            "reason": "same_note" if same_note else "choke_group_mismatch",
        }]
    return []


def build_mapping_trace(
    *, source_reference, assembled_events, physical_events, source_diagnostics,
    mapping_diagnostics, generation_mode, target_context,
):
    source_records = {
        int(item["source_event_index"]): item
        for item in source_diagnostics.get("source_events", [])
        if item.get("status") == "mapped"
    }
    target_kit = (target_context or {}).get("kit", {})
    target_pad_map = target_kit.get("pad_map", {})
    traces = []
    traced_ids = set()
    for physical in physical_events:
        trace_id = str(physical.get("mapping_trace_id") or "")
        try:
            assembled_index = int(trace_id.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            assembled_index = -1
        abstract = assembled_events[assembled_index] if 0 <= assembled_index < len(assembled_events) else {}
        source = source_records.get(int(abstract.get("source_event_index", -1)), {})
        note = int(physical["note"])
        pad = target_pad_map.get(str(note), {})
        evidence = source.get("role_evidence", "heuristic")
        role_confidence = float(source.get("role_confidence", 0.7 if evidence == "heuristic" else 0.0))
        fallback = not bool(source)
        mapping_confidence = min(role_confidence, float(pad.get("confidence", 1.0))) if generation_mode == "ableton_kit" else role_confidence
        trace = SourceMappingTrace(
            source_reference=source_reference,
            source_note=source.get("source_note"),
            source_position=float(source.get("source_position", abstract.get("beat", physical["beat"]))),
            source_duration=float(source.get("source_duration", abstract.get("duration", physical["duration"]))),
            source_velocity=int(source.get("source_velocity", abstract.get("velocity", physical["velocity"]))),
            resolved_role=str(abstract.get("role") or physical.get("role")),
            role_evidence=evidence,
            role_confidence=round(role_confidence, 6),
            target_role=str(physical.get("role")),
            target_note=note,
            target_pad_name=pad.get("label") or pad.get("original_name") or (str(physical.get("role")) if generation_mode == "standard_pack" else None),
            target_chain_name=pad.get("chain_name"),
            target_choke_group=pad.get("choke_group"),
            mapping_policy="ableton-kit-map.v1" if generation_mode == "ableton_kit" else "sensei-standard-drum-v1",
            mapping_confidence=round(mapping_confidence, 6),
            fallback_used=fallback,
            fallback_reason="generated_or_varied_event" if fallback else None,
            status="fallback" if fallback else "mapped",
            trace_id=trace_id or None,
        )
        traces.append(trace.to_dict())
        if trace_id:
            traced_ids.add(trace_id)

    mapped_traces = list(traces)
    skipped = [item for item in source_diagnostics.get("source_events", []) if item.get("status") == "skipped"]
    for item in skipped:
        traces.append(SourceMappingTrace(
            source_reference=source_reference,
            source_note=item.get("source_note"),
            source_position=float(item.get("source_position", 0.0)),
            source_duration=float(item.get("source_duration", 0.0)),
            source_velocity=int(item.get("source_velocity", 0)),
            resolved_role=None, role_evidence="unknown", role_confidence=0.0,
            target_role=None, target_note=None, target_pad_name=None,
            target_chain_name=None, target_choke_group=None,
            mapping_policy="unmapped-source-policy.v1", mapping_confidence=0.0,
            fallback_used=False, fallback_reason="unknown_source_role",
            status="skipped", trace_id=f"source_skipped_{item.get('source_event_index')}",
        ).to_dict())
    role_distribution = Counter(str(item.get("target_role")) for item in mapped_traces)
    evidence_distribution = Counter(str(item.get("role_evidence")) for item in traces)
    note_distribution = Counter(str(item.get("target_note")) for item in mapped_traces)
    role_to_note = mapping_diagnostics.get("role_to_note", {})
    choke_collisions = _choke_collisions(role_to_note, target_pad_map) if generation_mode == "ableton_kit" else []
    allowed_notes = set(int(value) for value in role_to_note.values())
    leaks = [
        {"trace_id": item.get("trace_id"), "source_note": item["source_note"], "target_note": item["target_note"]}
        for item in mapped_traces
        if item.get("source_note") == item.get("target_note") and int(item["target_note"]) not in allowed_notes
    ]
    source_count = int(source_diagnostics.get("input_event_count", len(traces)))
    unknown_count = int((source_diagnostics.get("evidence_counts") or {}).get("unknown", 0))
    fallback_count = sum(1 for item in mapped_traces if item.get("fallback_used"))
    confidence = sum(float(item["mapping_confidence"]) for item in mapped_traces) / len(mapped_traces) if mapped_traces else 0.0
    core = {item.get("resolved_role") for item in mapped_traces} & {"kick", "snare", "closed_hat"}
    report = MappingInvariantReport(
        source_event_count=source_count,
        mapped_event_count=len(mapped_traces),
        skipped_event_count=len(skipped),
        unknown_event_count=unknown_count,
        fallback_event_count=fallback_count,
        physical_event_count=len(physical_events),
        role_distribution=dict(role_distribution), evidence_distribution=dict(evidence_distribution),
        target_note_distribution=dict(note_distribution), core_role_coverage=round(len(core) / 3, 6),
        mapping_confidence=round(confidence, 6),
        mapping_collisions=tuple(mapping_diagnostics.get("mapping_collisions", [])),
        physical_duplicates=tuple(mapping_diagnostics.get("physical_duplicates_removed", [])),
        choke_collisions=tuple(choke_collisions),
        unmapped_roles=tuple(mapping_diagnostics.get("unavailable_roles_skipped", [])),
        source_note_leaks=tuple(leaks),
    ).to_dict()
    collision_classes = {
        "valid_layering": _valid_layering(physical_events),
        "duplicate_collision": list(mapping_diagnostics.get("physical_duplicates_removed", [])),
        "mapping_collision": list(mapping_diagnostics.get("mapping_collisions", [])),
        "choke_collision": choke_collisions,
        "unmapped_role": list(mapping_diagnostics.get("unavailable_roles_skipped", [])),
        "unknown_source_role": [dict(item) for item in skipped],
        "source_note_leak": leaks,
    }
    return traces, report, collision_classes
