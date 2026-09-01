from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aimixmaster.als_io import load_als, save_als_atomic
from aimixmaster.buss_builder import EXPECTED_DRUM_BUSS_DEVICE_TAGS, build_drum_buss
from aimixmaster.project_analyzer import direct_devices, find_unique_track, preservation_snapshot
from aimixmaster.verification import verify_drum_buss
from aimixmaster.template_exporter import export_boom_bap_95_drum_bus
import build_drum_buss as build_drum_buss_cli


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "SampleChopVol1/Ummu Gulsum/ummu gulsum Project/Golden Step_RECOVERED.als"
BACKUP = PROJECT.with_suffix(".als.aimixmaster-backup")


class DrumBussBuilderRegressionTest(unittest.TestCase):
    def test_verified_fixture_and_backup_preserve_mix_state(self) -> None:
        self.assertTrue(PROJECT.exists(), PROJECT)
        self.assertTrue(BACKUP.exists(), BACKUP)
        baseline = preservation_snapshot(load_als(BACKUP).getroot())
        proof = verify_drum_buss(PROJECT, preserved_before=baseline)
        self.assertEqual(proof.device_tags, EXPECTED_DRUM_BUSS_DEVICE_TAGS)

    def test_backup_build_then_second_run_is_a_no_op(self) -> None:
        tree = load_als(BACKUP)
        baseline = preservation_snapshot(tree.getroot())
        first = build_drum_buss(tree.getroot())
        self.assertTrue(first.changed)
        target = find_unique_track(tree.getroot(), "DRUM BUSS")
        self.assertEqual(
            tuple(device.tag for device in direct_devices(target.element)),
            EXPECTED_DRUM_BUSS_DEVICE_TAGS,
        )
        self.assertEqual(preservation_snapshot(tree.getroot()), baseline)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "regression.als"
            save_als_atomic(tree, output)
            verify_drum_buss(output, preserved_before=baseline)
            reloaded = load_als(output)
            before_second_run = preservation_snapshot(reloaded.getroot())
            second = build_drum_buss(reloaded.getroot())
            self.assertFalse(second.changed)
            self.assertEqual(preservation_snapshot(reloaded.getroot()), before_second_run)

    def test_template_exports_verified_direct_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "template.json"
            export_boom_bap_95_drum_bus(PROJECT, output)
            template = output.read_text(encoding="utf-8")
            self.assertIn('"EQ Eight"', template)
            self.assertIn('"Glue Compressor"', template)
            self.assertIn('"Utility"', template)

    def test_existing_identical_backup_can_be_reused_after_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "restored.als"
            backup = project.with_suffix(".als.aimixmaster-backup")
            project.write_bytes(BACKUP.read_bytes())
            backup.write_bytes(BACKUP.read_bytes())
            with patch("sys.argv", ["build_drum_buss.py", str(project), "--apply"]):
                self.assertEqual(build_drum_buss_cli.main(), 0)
            self.assertEqual(
                verify_drum_buss(project).device_tags, EXPECTED_DRUM_BUSS_DEVICE_TAGS
            )


if __name__ == "__main__":
    unittest.main()
