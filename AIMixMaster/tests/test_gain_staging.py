from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import soundfile as sf

from aimixmaster.als_io import AlsFormatError, load_als
from aimixmaster.gain_staging import (
    MEASUREMENT_STATUS,
    analyze_gain_staging,
    add_audio_measurements,
    classify_master_processor,
    measure_audio_file,
)
from aimixmaster.render_workflow import build_render_manifest, validate_renders
from aimixmaster.live_meter import analyze_live_meter_log
from aimixmaster.meter_tap_investigation import comparison, hypotheses, migrate_legacy_eq_test, report as tap_report
from aimixmaster.live_meter_transfer import build_transfer, estimate_relative_db
import gain_stage


ROOT = Path(__file__).resolve().parents[1]
# A committed fixture, not a pointer to whichever project was open at the time.
# The pointer form passed only while that project stayed where it was left, and
# took all seven of these tests down with it the day it moved -- unnoticed,
# because the suite never ran them. Rebuild it with tests/make_fixture.py.
PROJECT = ROOT / "tests" / "fixtures" / "gain_staging.als"


def _track(tag: str, track_id: int, name: str, volume: str = "1", group_id: str = "-1") -> ET.Element:
    return ET.fromstring(
        f'''<{tag} Id="{track_id}"><Name><UserName Value="{name}" /></Name><TrackGroupId Value="{group_id}" />
        <DeviceChain><Mixer><Volume><Manual Value="{volume}" /></Volume><Sends /></Mixer>
        <AudioOutputRouting><Target Value="AudioOut/Main" /></AudioOutputRouting>
        <DeviceChain><Devices /></DeviceChain></DeviceChain></{tag}>'''
    )


class GainStagingTest(unittest.TestCase):
    def test_relative_transfer_is_bounded_monotonic_and_never_absolute(self) -> None:
        def result(raw: float) -> dict:
            return {"raw_max": raw}
        investigation = {"als_path": "/fixture.als", "als_sha256": "abc", "tests": [
            {"test": "BASELINE", "status": "completed", "result": result(.9210174679756165)},
            {"test": "FADER_MINUS12", "status": "completed", "result": result(.7631227374076843)},
            {"test": "UTILITY_MINUS12", "status": "completed", "result": result(.7631227970123291)},
            {"test": "EQ_OUTPUT_MINUS12", "status": "completed", "result": result(.7631227970123291)},
            {"test": "UTILITY_PLUS12", "status": "completed", "result": result(1.0)},
        ]}
        transfer = build_transfer(investigation)
        baseline, status, warning = estimate_relative_db(.9210174679756165, transfer)
        minus, _, _ = estimate_relative_db(.7631227970123291, transfer)
        clamped, clamp_status, clamp_warning = estimate_relative_db(1.0, transfer)
        unresolved, unresolved_status, _ = estimate_relative_db(0.0, transfer)
        middle, _, _ = estimate_relative_db((.7631227970123291 + .9210174679756165) / 2, transfer)
        self.assertAlmostEqual(baseline, 0.0, places=5)
        self.assertAlmostEqual(minus, -12.0, places=5)
        self.assertIsNone(clamped)
        self.assertEqual(clamp_status, "clamped_or_saturated")
        self.assertIn("clamp", clamp_warning)
        self.assertIsNone(unresolved)
        self.assertEqual(unresolved_status, "unresolved")
        self.assertGreater(middle, -12.0)
        self.assertLess(middle, 0.0)
        self.assertEqual(status, "provisional_relative_only")

    def test_live_meter_blank_als_name_uses_track_id_fallback(self) -> None:
        manifest = {"session_id": "session-blank", "included_tracks": [{"track_index": 0, "track_id": 42, "track_name": "", "live_track_name": "Live Name", "track_type": "AudioTrack"}]}
        events = [{"event": "session_started", "session_id": "session-blank"}, *[{"event": "measurement_sample", "session_id": "session-blank", "track_index": 0, "track_name": "Live Name", "meter_left": .5, "meter_right": .5, "monotonic_timestamp": float(i), "track_activator": True, "solo": False, "mute": False, "track_volume": 1.0} for i in range(10)], {"event": "session_completed", "session_id": "session-blank"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meter.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            report = analyze_live_meter_log(path, manifest)
        self.assertEqual(report["tracks"][0]["track_name"], "unresolved_track_42")
        self.assertEqual(report["track_identity_mapping"][0]["mapping_status"], "unresolved_als_track_name")

    def test_legacy_eq_plus12_session_migrates_without_moving_log(self) -> None:
        legacy = {"manifest_path": "/reports/sessions/05_eq_output_plus12/live_measurement_manifest.json", "log_path": "/reports/sessions/05_eq_output_plus12/live_meter.jsonl"}
        study = {"test_manifests": {"EQ_OUTPUT_PLUS12": legacy}}
        self.assertTrue(migrate_legacy_eq_test(study))
        self.assertNotIn("EQ_OUTPUT_PLUS12", study["test_manifests"])
        migrated = study["test_manifests"]["EQ_OUTPUT_MINUS12"]
        self.assertEqual(migrated["log_path"], legacy["log_path"])
        self.assertIn("PLUS12", migrated["migration_warning"])

    def test_meter_tap_investigation_compares_raw_controls_without_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def manifest(session_id: str, values: list[float]) -> dict:
                path = root / f"{session_id}.jsonl"
                events = [{"event": "session_started", "session_id": session_id}]
                events += [{"event": "measurement_sample", "session_id": session_id, "track_index": 2, "track_name": "3-calibration_reference", "meter_left": value, "meter_right": value / 2, "monotonic_timestamp": float(i)} for i, value in enumerate(values)]
                events += [{"event": "session_completed", "session_id": session_id}]
                path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
                return {"session_id": session_id, "log_path": str(path), "included_tracks": [{"track_index": 2, "track_name": "calibration_reference", "live_track_name": "3-calibration_reference"}]}
            baseline = manifest("baseline", [1.0] * 12)
            fader = manifest("fader", [.5] * 12)
            study = {"als_path": "/fixture.als", "als_sha256": "abc", "baseline_manifest": baseline, "test_manifests": {"FADER_MINUS12": fader, "SEND_RETURN_ON": {"status": "skipped_not_configured", "warning": "No documented return/send test configuration"}}}
            data = tap_report(study, root)
        row = next(item["result"] for item in data["tests"] if item["test"] == "FADER_MINUS12")
        self.assertEqual(row["ratio_to_baseline"], .5)
        self.assertAlmostEqual(row["delta_db_equivalent"], -6.0206, places=3)
        self.assertNotIn("model_type", json.dumps(data).lower())
        post = next(item for item in hypotheses({"FADER_MINUS12": row}) if item["hypothesis"] == "post-fader")
        self.assertEqual(post["confidence"], "provisional")
        send = next(item for item in data["tests"] if item["test"] == "SEND_RETURN_ON")
        self.assertEqual(send["status"], "skipped_not_configured")
        self.assertEqual(send["warning"], "No documented return/send test configuration")

    def _live_manifest(self) -> dict:
        return {"session_id": "session-1", "included_tracks": [{"track_index": 0, "track_id": 10, "track_name": "KICK", "live_track_name": "KICK", "track_type": "AudioTrack"}]}

    def test_live_meter_raw_stereo_and_deterministic_report(self) -> None:
        events = [
            {"event": "session_started", "session_id": "session-1"},
            *[{"event": "measurement_sample", "session_id": "session-1", "track_index": 0, "track_name": "KICK", "meter_left": 0.1, "meter_right": 0.2, "monotonic_timestamp": float(i), "track_activator": True, "solo": False, "mute": False, "track_volume": 1.0} for i in range(10)],
            {"event": "session_completed", "session_id": "session-1"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meter.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
            first = analyze_live_meter_log(path, self._live_manifest())
            second = analyze_live_meter_log(path, self._live_manifest())
        row = first["tracks"][0]
        self.assertEqual(row["raw_max_stereo"], 0.2)
        self.assertIsNone(row["max_peak_dbfs"])
        self.assertIsNone(row["proposed_adjustment_db"])
        self.assertEqual(row["policy_status"], "blocked_pending_meter_calibration")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_live_meter_rejects_incomplete_wrong_session_and_state_change(self) -> None:
        events = [{"event": "measurement_sample", "session_id": "wrong", "track_index": 0, "track_name": "KICK", "meter_left": 0.1, "meter_right": 0.1, "monotonic_timestamp": 0.0, "track_activator": True, "solo": False, "mute": False, "track_volume": 1.0}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
            report = analyze_live_meter_log(path, self._live_manifest())
        row = report["tracks"][0]
        self.assertEqual(row["measurement_status"], "invalid_or_incomplete")
        self.assertTrue(any("Session ID mismatch" in warning for warning in row["warnings"]))
    def _audio_root(self, name: str, audio_path: Path | None = None, tag: str = "AudioTrack") -> ET.Element:
        root = ET.Element("LiveSet")
        track = _track(tag, 1, name)
        if audio_path is not None:
            clip = ET.fromstring(
                f'''<AudioClip><SampleRef><FileRef><Path Value="{audio_path}" /></FileRef></SampleRef>
                <SampleVolume Value="1" /><IsWarped Value="false" /></AudioClip>'''
            )
            track.find("./DeviceChain").append(clip)
        root.extend([track, _track("MainTrack", 2, "")])
        return root

    def test_wav_peak_rms_stereo_and_silence_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stereo = folder / "stereo.wav"
            sf.write(stereo, np.array([[0.5, -0.5], [0.25, -0.25]], dtype=np.float32), 48000)
            result = measure_audio_file(stereo)
            self.assertAlmostEqual(result["peak"], -6.0206, places=3)
            self.assertIsNotNone(result["rms"])
            self.assertAlmostEqual(result["duration"], 2 / 48000, places=6)
            silent = folder / "silent.wav"
            sf.write(silent, np.zeros((16, 2), dtype=np.float32), 48000)
            silence = measure_audio_file(silent)
            self.assertIsNone(silence["peak"])
            self.assertTrue(any("digital silence" in warning for warning in silence["warnings"]))

    def test_clipping_source_is_measured_but_not_proposed_as_track_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "clip.wav"
            sf.write(audio, np.array([[-1.0], [1.0]], dtype=np.float32), 48000, subtype="FLOAT")
            root = self._audio_root("SOURCE", audio)
            report = add_audio_measurements(root, analyze_gain_staging(root), audio.with_suffix(".als"))
            row = report.tracks[0]
            self.assertEqual(row.measurement_scope, "source_file")
            self.assertEqual(row.measured_peak_dbfs, 0.0)
            self.assertIsNone(row.proposed_adjustment_db)
            self.assertEqual(row.proposed_method, "requires_track_render")

    def test_render_match_enables_peak_policy_and_ambiguous_render_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            root = self._audio_root("Lead Vox")
            render = folder / "Lead Vox.wav"
            sf.write(render, np.array([[0.5]], dtype=np.float32), 48000)
            report = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als", folder)
            row = report.tracks[0]
            self.assertEqual(row.measurement_scope, "rendered_track")
            self.assertEqual(row.proposed_method, "rendered_track_peak_no_change")
            self.assertEqual(row.proposed_adjustment_db, 0.0)
            second = folder / "LEAD-VOX.aiff"
            sf.write(second, np.array([[0.5]], dtype=np.float32), 48000, format="AIFF")
            ambiguous = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als", folder).tracks[0]
            self.assertEqual(ambiguous.measurement_scope, "unavailable")
            self.assertTrue(any("Ambiguous render" in warning for warning in ambiguous.measurement_warnings))

    def test_missing_source_and_midi_are_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "gone.wav"
            root = self._audio_root("MISSING", missing)
            report = add_audio_measurements(root, analyze_gain_staging(root), Path(directory) / "project.als")
            self.assertEqual(report.tracks[0].measurement_scope, "unavailable")
            self.assertTrue(any("unresolved" in warning for warning in report.tracks[0].measurement_warnings))
            midi_root = self._audio_root("MIDI", tag="MidiTrack")
            midi = add_audio_measurements(midi_root, analyze_gain_staging(midi_root), Path(directory) / "project.als").tracks[0]
            self.assertEqual(midi.measurement_scope, "unavailable")
            self.assertTrue(any("No direct audio" in warning for warning in midi.measurement_warnings))

    def test_audio_measurement_json_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            audio = folder / "stable.wav"
            sf.write(audio, np.array([[0.25], [-0.25]], dtype=np.float32), 48000)
            root = self._audio_root("STABLE", audio)
            first = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als").as_dict()
            second = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als").as_dict()
            self.assertEqual(
                json.dumps(first, ensure_ascii=False, sort_keys=True),
                json.dumps(second, ensure_ascii=False, sort_keys=True),
            )

    def test_manifest_names_are_deterministic_safe_and_deduplicated(self) -> None:
        root = ET.Element("LiveSet")
        root.extend([_track("AudioTrack", 1, "KİCK"), _track("AudioTrack", 2, "Kick"), _track("MidiTrack", 3, "Piano"), _track("MainTrack", 4, "")])
        manifest = build_render_manifest(root, "demo")
        names = [entry["export_filename"] for entry in manifest["tracks"]]
        self.assertEqual(names[:2], ["01_kick.wav", "02_kick__2.wav"])
        self.assertTrue(manifest["tracks"][0]["should_render"])
        self.assertFalse(manifest["tracks"][2]["should_render"])

    def test_render_validation_exact_missing_extra_invalid_silent_clipped_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            root = ET.Element("LiveSet")
            root.extend([_track("AudioTrack", 1, "Kick"), _track("AudioTrack", 2, "Snare"), _track("MainTrack", 3, "")])
            manifest = build_render_manifest(root, "demo")
            sf.write(folder / "01_kick.wav", np.array([[1.0]], dtype=np.float32), 48000, subtype="FLOAT")
            (folder / "99_duplicate.wav").write_bytes((folder / "01_kick.wav").read_bytes())
            sf.write(folder / "extra.wav", np.zeros((4, 1), dtype=np.float32), 48000)
            (folder / "02_snare.wav").write_bytes(b"not wav")
            validation = validate_renders(manifest, folder)
            kick = validation["tracks"][0]
            snare = validation["tracks"][1]
            self.assertEqual(kick["match_method"], "manifest_export_filename")
            self.assertEqual(snare["confidence"], "high")
            self.assertTrue(any("cannot be opened" in warning for warning in snare["warnings"]))
            self.assertIn(str(folder / "extra.wav"), validation["extra_files"])
            self.assertTrue(any("Physical duplicate" in warning for warning in kick["warnings"]))

    def test_render_policy_attenuates_never_boosts_and_ambiguous_names_are_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            root = self._audio_root("Kick")
            render = folder / "Kick.wav"
            sf.write(render, np.array([[1.0]], dtype=np.float32), 48000, subtype="FLOAT")
            clipped = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als", folder).tracks[0]
            self.assertEqual(clipped.proposed_adjustment_db, -6.0)
            sf.write(render, np.array([[0.01]], dtype=np.float32), 48000)
            low = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als", folder).tracks[0]
            self.assertIsNone(low.proposed_adjustment_db)
            sf.write(folder / "KICK.aiff", np.array([[1.0]], dtype=np.float32), 48000, format="AIFF")
            ambiguous = add_audio_measurements(root, analyze_gain_staging(root), folder / "project.als", folder).tracks[0]
            self.assertEqual(ambiguous.confidence, "none")
    def test_fixture_utility_encoding_is_not_assumed_to_be_db(self) -> None:
        report = analyze_gain_staging(load_als(PROJECT).getroot())
        drum = next(track for track in report.tracks if track.track_name == "DRUM BUSS")
        self.assertIsNone(drum.utility_gain_db)
        self.assertIsNone(drum.proposed_adjustment_db)
        self.assertEqual(drum.proposed_method, "requires_audio_measurement")
        self.assertEqual(drum.confidence, "none")
        self.assertEqual(drum.measurement_status, MEASUREMENT_STATUS)
        self.assertTrue(any("ALS encoding is unverified" in warning for warning in drum.warnings))

    def test_effective_gain_contains_only_proven_fader(self) -> None:
        report = analyze_gain_staging(load_als(PROJECT).getroot())
        drum = next(track for track in report.tracks if track.track_name == "DRUM BUSS")
        self.assertEqual(drum.current_fader_db, 0.0)
        self.assertEqual(drum.effective_known_gain_db, 0.0)
        self.assertTrue(any("excludes unverified Utility" in warning for warning in drum.warnings))

    def test_parent_resolution_distinguishes_group_master_and_unresolved(self) -> None:
        root = ET.Element("LiveSet")
        group = _track("GroupTrack", 10, "BUS")
        child = _track("AudioTrack", 11, "CHILD", group_id="10")
        unresolved = _track("AudioTrack", 12, "ORPHAN", group_id="99")
        root.extend([group, child, unresolved, _track("MainTrack", 13, "")])
        report = analyze_gain_staging(root)
        rows = {track.track_name: track for track in report.tracks}
        self.assertEqual((rows["BUS"].parent_bus, rows["BUS"].routing_kind), ("master", "master"))
        self.assertEqual((rows["CHILD"].parent_bus, rows["CHILD"].routing_kind), ("BUS", "direct_group"))
        self.assertEqual((rows["ORPHAN"].parent_bus, rows["ORPHAN"].routing_kind), ("unresolved_group_id:99", "unresolved"))

    def test_master_processor_uses_exact_identity_not_substring(self) -> None:
        self.assertEqual(classify_master_processor(ET.Element("Limiter")), "limiter")
        self.assertEqual(classify_master_processor(ET.Element("GlueCompressor")), "compressor")
        self.assertEqual(classify_master_processor(ET.Element("Dynamics")), "unknown_dynamics")
        self.assertIsNone(classify_master_processor(ET.Element("MyLimiterLikeDevice")))

    def test_dry_run_writes_reports_without_modifying_source(self) -> None:
        before = hashlib.sha256(PROJECT.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports"
            with patch("sys.argv", ["gain_stage.py", str(PROJECT), "--dry-run", "--measure-audio", "--output-dir", str(output)]):
                self.assertEqual(gain_stage.main(), 0)
            report_dir = next(output.iterdir())
            payload = json.loads((report_dir / "gain_stage.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertTrue((report_dir / "gain_stage.md").exists())
        self.assertEqual(hashlib.sha256(PROJECT.read_bytes()).hexdigest(), before)

    def test_cli_rejects_missing_non_als_invalid_gzip_and_invalid_xml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cases = {
                folder / "missing.als": None,
                folder / "wrong.txt": b"not als",
                folder / "bad-gzip.als": b"not gzip",
                folder / "bad-xml.als": gzip.compress(b"<LiveSet>"),
            }
            for path, content in cases.items():
                if content is not None:
                    path.write_bytes(content)
                with patch("sys.argv", ["gain_stage.py", str(path), "--dry-run"]):
                    self.assertEqual(gain_stage.main(), 1)


if __name__ == "__main__":
    unittest.main()
