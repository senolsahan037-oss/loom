"""Local adapter between the SDK extension and Sensei's Generate contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--context", help="Verified current-project context JSON")
    source.add_argument("--live-target", help="Live SDK target evidence JSON")
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()
    try:
        if args.live_target:
            target = json.loads(Path(args.live_target).read_text(encoding="utf-8"))
            from core.live_context_resolver import resolve_live_context
            from core.midi_runtime import _release_artifacts
            artifacts = _release_artifacts(Path(args.data_root).expanduser().resolve())
            context = resolve_live_context(target, identity_path=artifacts["preset_genre_identities"], graph_path=artifacts["genre_neighbor_graph"])
        else:
            context = json.loads(Path(args.context).read_text(encoding="utf-8"))
        from core.generate_gate import evaluate_generate
        print(json.dumps(evaluate_generate(context, data_root=args.data_root), ensure_ascii=False))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"schema_version": "sensei.generate-report.v1", "status": "blocked", "write_authorized": False, "payload": None, "report": {"code": "project_context_unreadable", "detail": str(error), "checks": []}}))


if __name__ == "__main__":
    main()
