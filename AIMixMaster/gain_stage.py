#!/usr/bin/env python3
"""Create read-only structural gain-staging dry-run reports for an ALS file."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys
import uuid

from aimixmaster.als_io import AlsFormatError, load_als
from aimixmaster.gain_staging import add_audio_measurements, analyze_gain_staging, markdown_report
from aimixmaster.gain_staging_v2 import analyze_gain_staging_v2, markdown_v2
from aimixmaster.clip_alignment import analyze_clip_alignment, markdown_clip_alignment
from aimixmaster.render_workflow import build_render_manifest, manifest_markdown, render_map_from_validation, validate_renders, validation_markdown
from aimixmaster.project_analyzer import analyze_tracks
from aimixmaster.live_meter import analyze_live_meter_log, live_meter_markdown, METER_UNIT, REQUESTED_SAMPLE_RATE_HZ
from aimixmaster.live_meter_calibration import calibrate, calibration_markdown, find_latest_completed_session
from aimixmaster.meter_tap_investigation import TESTS as TAP_TESTS, INSTRUCTIONS as TAP_INSTRUCTIONS, _completed as tap_completed, markdown as tap_markdown, report as tap_report, migrate_legacy_eq_test
from aimixmaster.live_meter_transfer import apply_transfer_to_report, build_transfer, transfer_markdown, transfer_svg


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_for_log(log_path: Path, reports_dir: Path) -> dict:
    target = log_path.resolve()
    for path in reports_dir.glob("**/live_measurement_manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if Path(manifest.get("log_path", "")).resolve() == target:
            return manifest
    raise RuntimeError("No live-measurement manifest references the supplied JSONL log")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("als", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Create gain-staging reports without writing the ALS")
    parser.add_argument("--engine-v2", action="store_true", help="Create deterministic-first, read-only Gain Staging Engine v2 report")
    parser.add_argument("--clip-alignment-dry-run", action="store_true", help="Create read-only intra-track clip alignment report")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--measure-audio", action="store_true", help="Measure resolved audio files and optional track renders")
    parser.add_argument("--renders-dir", type=Path, help="Directory containing exact-name rendered track files")
    parser.add_argument("--prepare-renders", action="store_true", help="Create deterministic individual-track export manifest")
    parser.add_argument("--validate-renders", action="store_true", help="Validate a render directory against the manifest")
    parser.add_argument("--prepare-live-measurement", action="store_true", help="Create a MixConsoleLive2 live-meter session request")
    parser.add_argument("--analyze-live-meter-log", type=Path, help="Analyze append-only MixConsoleLive2 session JSONL")
    parser.add_argument("--session-manifest", type=Path, help="Manifest produced by --prepare-live-measurement")
    parser.add_argument("--calibrate-live-meter", action="store_true", help="Calibrate from the latest completed session for this ALS")
    parser.add_argument("--analyze-latest-live-session", action="store_true", help="Analyze the latest completed session for this ALS")
    parser.add_argument("--meter-tap-investigation", action="store_true", help="Prepare/progress controlled raw-meter tap-point experiments")
    parser.add_argument("--build-live-meter-transfer", action="store_true", help="Build a bounded provisional relative-meter transfer from completed tap controls")
    parser.add_argument("--apply-live-meter-transfer", type=Path, help="Apply the relative-meter transfer to a session JSONL or live-meter report")
    args = parser.parse_args()
    try:
        if args.als.suffix.lower() != ".als":
            raise AlsFormatError(f"Expected an .als file: {args.als}")
        before = _sha256(args.als) if args.als.is_file() else None
        tree = load_als(args.als)
        if not (args.dry_run or args.engine_v2 or args.clip_alignment_dry_run or args.prepare_renders or args.validate_renders or args.prepare_live_measurement or args.analyze_live_meter_log or args.calibrate_live_meter or args.analyze_latest_live_session or args.meter_tap_investigation or args.build_live_meter_transfer or args.apply_live_meter_transfer):
            raise RuntimeError("Choose a report, render, or live-measurement action")
        if args.validate_renders and args.renders_dir is None:
            raise RuntimeError("--validate-renders requires --renders-dir")
        if args.analyze_live_meter_log and args.session_manifest is None:
            raise RuntimeError("--analyze-live-meter-log requires --session-manifest")
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if args.build_live_meter_transfer:
            investigation_path = args.output_dir / "meter_tap_investigation" / "meter_tap_investigation.json"
            if not investigation_path.is_file():
                raise RuntimeError("completed meter_tap_investigation.json is required")
            investigation = json.loads(investigation_path.read_text(encoding="utf-8"))
            if investigation.get("als_sha256") != before:
                raise RuntimeError("meter tap investigation belongs to a different ALS revision")
            transfer = build_transfer(investigation)
            destination = args.output_dir / "live_meter_transfer"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "live_meter_transfer.json").write_text(json.dumps(transfer, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_meter_transfer.md").write_text(transfer_markdown(transfer), encoding="utf-8")
            (destination / "live_meter_transfer.svg").write_text(transfer_svg(transfer), encoding="utf-8")
            print(f"Relative transfer JSON: {destination / 'live_meter_transfer.json'}")
        if args.apply_live_meter_transfer:
            transfer_path = args.output_dir / "live_meter_transfer" / "live_meter_transfer.json"
            if not transfer_path.is_file():
                raise RuntimeError("run --build-live-meter-transfer first")
            transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
            source = args.apply_live_meter_transfer
            if source.suffix.lower() == ".jsonl":
                manifest = _manifest_for_log(source, args.output_dir)
                live = analyze_live_meter_log(source, manifest, relative_transfer=transfer)
            else:
                live = apply_transfer_to_report(json.loads(source.read_text(encoding="utf-8")), transfer)
            destination = args.output_dir / "live_meter_transfer" / f"applied_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "live_meter_relative_report.json").write_text(json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_meter_relative_report.md").write_text(live_meter_markdown(live), encoding="utf-8")
            print(f"Relative live report JSON: {destination / 'live_meter_relative_report.json'}")
        if args.meter_tap_investigation:
            destination = args.output_dir / "meter_tap_investigation"
            destination.mkdir(parents=True, exist_ok=True)
            study_path = destination / "meter_tap_investigation_state.json"
            if study_path.is_file():
                study = json.loads(study_path.read_text(encoding="utf-8"))
                if study.get("als_path") != str(args.als.resolve()) or study.get("als_sha256") != before:
                    raise RuntimeError("existing meter-tap investigation belongs to a different ALS revision")
                if migrate_legacy_eq_test(study):
                    study_path.write_text(json.dumps(study, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            else:
                baseline_path, _, baseline = find_latest_completed_session(args.als, args.output_dir)
                study = {"schema_version": "1.0", "study_id": str(uuid.uuid4()), "als_path": str(args.als.resolve()), "als_sha256": before, "baseline_manifest_path": str(baseline_path.resolve()), "baseline_manifest": baseline, "test_manifests": {}}
                study_path.write_text(json.dumps(study, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return_config = study["baseline_manifest"].get("return_send_test_configuration", study["baseline_manifest"].get("return_send_test", {}))
            required_return_fields = {"return_track_name", "source_track_name", "send_index", "send_level_db"}
            if not isinstance(return_config, dict) or not required_return_fields <= set(return_config):
                prior = study["test_manifests"].get("SEND_RETURN_ON", {})
                if prior.get("status") != "skipped_not_configured":
                    # Preserve an already-created manifest/log record; only mark it ineligible.
                    study["test_manifests"]["SEND_RETURN_ON"] = prior | {"status": "skipped_not_configured", "warning": "No documented return/send test configuration"}
                    study_path.write_text(json.dumps(study, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            pending = None
            for test in TAP_TESTS[1:]:
                manifest = study["test_manifests"].get(test)
                if manifest and manifest.get("status") == "skipped_not_configured":
                    continue
                if manifest is None or not tap_completed(manifest):
                    pending = test
                    break
            progress = tap_report(study, destination)
            (destination / "meter_tap_investigation.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "meter_tap_investigation.md").write_text(tap_markdown(progress), encoding="utf-8")
            if pending is None:
                print(f"Tap investigation JSON: {destination / 'meter_tap_investigation.json'}")
                print(f"Tap investigation Markdown: {destination / 'meter_tap_investigation.md'}")
            elif pending in study["test_manifests"]:
                manifest = study["test_manifests"][pending]
                print(f"TEST {TAP_TESTS.index(pending) + 1} / {len(TAP_TESTS)}: {pending}")
                print("Waiting for its completed Live session; do not create a second session.")
                print(f"Session manifest: {manifest['manifest_path']}")
            else:
                sequence = TAP_TESTS.index(pending) + 1
                test_dir = destination / "sessions" / f"{sequence:02d}_{pending.lower()}"
                test_dir.mkdir(parents=True, exist_ok=False)
                session_id = str(uuid.uuid4())
                log_path = (test_dir / f"live_meter_{session_id}.jsonl").resolve()
                manifest_path = test_dir / "live_measurement_manifest.json"
                request_path = (args.output_dir / "live_meter_active_session.json").resolve()
                included = study["baseline_manifest"]["included_tracks"]
                manifest = {"schema_version": "1.0", "session_id": session_id, "als_path": str(args.als.resolve()), "als_sha256": before, "meter_unit": METER_UNIT, "requested_sample_rate_hz": REQUESTED_SAMPLE_RATE_HZ, "log_path": str(log_path), "session_request_path": str(request_path), "included_tracks": included, "excluded_tracks": study["baseline_manifest"].get("excluded_tracks", []), "meter_tap_investigation": {"study_id": study["study_id"], "test": pending}, "manifest_path": str(manifest_path.resolve())}
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                request_path.write_text(json.dumps({"schema_version": "1.0", "session_id": session_id, "log_path": str(log_path), "expected_tracks": included, "meter_tap_investigation": {"study_id": study["study_id"], "test": pending}}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                (test_dir / "instructions.md").write_text(f"# Meter Tap Test {sequence} / {len(TAP_TESTS)}\n\n## {pending}\n\n{TAP_INSTRUCTIONS[pending]}\n\n1. Apply only this change.\n2. Play the complete calibration arrangement once, then stop it.\n3. Restore the baseline setting before running this command again.\n\nNo ALS file is written by this tool.\n", encoding="utf-8")
                study["test_manifests"][pending] = manifest
                study_path.write_text(json.dumps(study, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(f"TEST {sequence} / {len(TAP_TESTS)}: {pending}")
                print(TAP_INSTRUCTIONS[pending])
                print(f"Instructions: {test_dir / 'instructions.md'}")
                print(f"Session manifest: {manifest_path}")
        if args.calibrate_live_meter:
            _, log_path, manifest = find_latest_completed_session(args.als, args.output_dir)
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            profile = calibrate(events, manifest)
            destination = args.output_dir / "live_meter_calibration"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "live_meter_calibration.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_meter_calibration.md").write_text(calibration_markdown(profile), encoding="utf-8")
            print(f"Calibration JSON: {destination / 'live_meter_calibration.json'}")
            print(f"Calibration Markdown: {destination / 'live_meter_calibration.md'}")
        if args.analyze_latest_live_session:
            _, log_path, manifest = find_latest_completed_session(args.als, args.output_dir)
            profile_path = args.output_dir / "live_meter_calibration" / "live_meter_calibration.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else None
            live = analyze_live_meter_log(log_path, manifest, profile)
            destination = args.output_dir / f"live_meter_analysis_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "live_meter_analysis.json").write_text(json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_meter_analysis.md").write_text(live_meter_markdown(live), encoding="utf-8")
            print(f"Latest live analysis JSON: {destination / 'live_meter_analysis.json'}")
        report = analyze_gain_staging(tree.getroot())
        if args.measure_audio:
            render_paths = None
            if args.renders_dir:
                render_paths = render_map_from_validation(validate_renders(build_render_manifest(tree.getroot(), args.als.stem), args.renders_dir))
            report = add_audio_measurements(tree.getroot(), report, args.als, args.renders_dir, render_paths)
        after = _sha256(args.als)
        if before != after:
            raise RuntimeError("Dry-run changed the source ALS SHA-256")
        if args.prepare_renders:
            manifest = build_render_manifest(tree.getroot(), args.als.stem)
            destination = args.output_dir / f"render_manifest_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "render_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "render_manifest.md").write_text(manifest_markdown(manifest), encoding="utf-8")
            (destination / "ABLETON_EXPORT_STEPS.md").write_text(
                "# Ableton Export Steps\n\n"
                "1. Open the set and use the manifest export filenames for individual AudioTrack renders.\n"
                "2. Export WAV or AIFF with the project sample rate; do not normalize.\n"
                "3. Export each eligible track post-device-chain, keeping the documented fader state.\n"
                "4. Place files in one empty render folder without renaming them.\n"
                "5. Run `--validate-renders --renders-dir <folder>` before requesting gain suggestions.\n",
                encoding="utf-8",
            )
            print(f"Manifest JSON: {destination / 'render_manifest.json'}")
            print(f"Manifest Markdown: {destination / 'render_manifest.md'}")
        if args.validate_renders:
            manifest = build_render_manifest(tree.getroot(), args.als.stem)
            validation = validate_renders(manifest, args.renders_dir)
            destination = args.output_dir / f"render_validation_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "render_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "render_validation.md").write_text(validation_markdown(validation), encoding="utf-8")
            print(f"Validation JSON: {destination / 'render_validation.json'}")
            print(f"Validation Markdown: {destination / 'render_validation.md'}")
        if args.prepare_live_measurement:
            destination = args.output_dir / f"live_measurement_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            infos = [item for item in analyze_tracks(tree.getroot()) if item.track_type not in {"ReturnTrack", "MainTrack"}]
            included, excluded = [], []
            for index, item in enumerate(infos):
                effective = item.element.find("./Name/EffectiveName")
                live_name = effective.attrib.get("Value", item.name) if effective is not None else item.name
                row = {"track_index": index, "track_id": item.track_id, "track_name": item.name, "live_track_name": live_name, "track_type": item.track_type}
                (included if item.track_type in {"AudioTrack", "MidiTrack"} else excluded).append(row)
            session_id = str(uuid.uuid4())
            log_path = (destination / f"live_meter_{session_id}.jsonl").resolve()
            manifest_path = destination / "live_measurement_manifest.json"
            request_path = (args.output_dir / "live_meter_active_session.json").resolve()
            manifest = {"schema_version": "1.0", "session_id": session_id, "als_path": str(args.als.resolve()), "als_sha256": before, "meter_unit": METER_UNIT, "requested_sample_rate_hz": REQUESTED_SAMPLE_RATE_HZ, "log_path": str(log_path), "session_request_path": str(request_path), "included_tracks": included, "excluded_tracks": excluded}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            request_path.write_text(json.dumps({"schema_version": "1.0", "session_id": session_id, "log_path": str(log_path), "expected_tracks": included}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_measurement_instructions.md").write_text("# Live Measurement\n\n1. Configure/reload `MixConsoleLive2` in Ableton.\n2. Open the project, go to arrangement start, then start playback once.\n3. Let playback finish and stop it once.\n4. Run the log analysis command printed by this tool.\n\nNo faders, devices, or ALS files are changed.\n", encoding="utf-8")
            print(f"Live manifest: {manifest_path}")
            print(f"Instructions: {destination / 'live_measurement_instructions.md'}")
            print(f"Session request: {request_path}")
        if args.analyze_live_meter_log:
            manifest = json.loads(args.session_manifest.read_text(encoding="utf-8"))
            profile_path = args.output_dir / "live_meter_calibration" / "live_meter_calibration.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else None
            live = analyze_live_meter_log(args.analyze_live_meter_log, manifest, profile)
            destination = args.output_dir / f"live_meter_analysis_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "live_meter_analysis.json").write_text(json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "live_meter_analysis.md").write_text(live_meter_markdown(live), encoding="utf-8")
            print(f"Live analysis JSON: {destination / 'live_meter_analysis.json'}")
            print(f"Live analysis Markdown: {destination / 'live_meter_analysis.md'}")
        if args.dry_run:
            destination = args.output_dir / f"gain_stage_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            (destination / "gain_stage.json").write_text(
                json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (destination / "gain_stage.md").write_text(markdown_report(report), encoding="utf-8")
            print(f"JSON: {destination / 'gain_stage.json'}")
            print(f"Markdown: {destination / 'gain_stage.md'}")
        if args.engine_v2:
            destination = args.output_dir / f"gain_staging_v2_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            v2 = analyze_gain_staging_v2(tree.getroot(), args.als)
            (destination / "gain_staging_v2.json").write_text(json.dumps(v2, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "gain_staging_v2.md").write_text(markdown_v2(v2), encoding="utf-8")
            print(f"V2 JSON: {destination / 'gain_staging_v2.json'}")
            print(f"V2 Markdown: {destination / 'gain_staging_v2.md'}")
        if args.clip_alignment_dry_run:
            destination = args.output_dir / f"clip_alignment_{stamp}"
            destination.mkdir(parents=True, exist_ok=False)
            alignment = analyze_clip_alignment(tree.getroot(), args.als)
            (destination / "clip_alignment_dry_run.json").write_text(json.dumps(alignment, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (destination / "clip_alignment_dry_run.md").write_text(markdown_clip_alignment(alignment), encoding="utf-8")
            print(f"Clip alignment JSON: {destination / 'clip_alignment_dry_run.json'}")
        print(f"Source SHA-256: {after}")
        return 0
    except (AlsFormatError, OSError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
