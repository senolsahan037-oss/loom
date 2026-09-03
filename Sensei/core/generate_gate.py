"""Fail-closed contract for Sensei's single Generate action."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.midi_runtime import prepare_midi_variation


SCHEMA_VERSION = "sensei.generate-report.v1"


def _blocked(code: str, *, detail: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "write_authorized": False,
        "report": {"code": code, "detail": detail, "checks": checks},
        "payload": None,
    }


def evaluate_generate(project_context: dict[str, Any], *, data_root: str | Path | None = None) -> dict[str, Any]:
    """Prepare one write, or explain exactly why no write is authorized.

    The context is expected to be supplied by Sensei's project memory, not
    inferred from a Live track name at button-press time.
    """
    checks: list[dict[str, Any]] = []
    target_context = project_context.get("target_context")
    if not isinstance(target_context, dict):
        return _blocked("project_target_context_missing", detail="Sensei has no verified target context for this clip slot.", checks=checks)
    checks.append({"name": "project_target_context", "passed": True})
    genre = project_context.get("genres") if project_context.get("genre_mode") == "synthesis" else project_context.get("genre")
    valid_genre = (isinstance(genre, str) and bool(genre.strip())) or (isinstance(genre, list) and len(genre) > 1 and all(isinstance(value, str) and value.strip() for value in genre))
    if not valid_genre:
        return _blocked("project_genre_missing", detail="Sensei has no resolved genre for this generation.", checks=checks)
    checks.append({"name": "project_genre", "passed": True, "value": genre})
    for field in ("bars", "seed"):
        if not isinstance(project_context.get(field), int):
            return _blocked(f"project_{field}_missing", detail=f"Sensei has no valid {field} for this generation.", checks=checks)
        checks.append({"name": f"project_{field}", "passed": True, "value": project_context[field]})

    result = prepare_midi_variation(
        target_context=target_context,
        genre=genre,
        bars=project_context["bars"],
        seed=project_context["seed"],
        variation_amount=float(project_context.get("variation_amount", 0.35)),
        target_root=project_context.get("target_root"),
        target_mode=project_context.get("target_mode"),
        exclude_reference_ids=project_context.get("exclude_reference_ids"),
        density=project_context.get("density"),
        genre_style=project_context.get("genre_style"),
        data_root=data_root,
    )
    if not result["generation_safe"]:
        return _blocked(result["error"] or "generation_unsafe", detail="The Generate contract rejected the request; no MIDI was changed.", checks=checks + [{"name": "generation", "passed": False, "resolution": result.get("resolution")}])
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready_to_write",
        "write_authorized": True,
        "report": {"code": "ready", "detail": "Target, dataset and generated MIDI passed all checks.", "checks": checks + [{"name": "generation", "passed": True, "resolution": result["resolution"]}]},
        "payload": result["payload"],
        # What the section evidence actually did -- density band, genre fit --
        # travels out with the payload; a passthrough nobody can observe is
        # indistinguishable from one that silently does nothing.
        "diagnostics": result.get("diagnostics"),
    }
