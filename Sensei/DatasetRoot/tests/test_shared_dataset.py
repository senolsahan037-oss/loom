from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dataset.reader import DatasetIntegrityError, SharedDataset


ROOT = Path(__file__).resolve().parents[1]


class SharedDatasetTests(unittest.TestCase):
    def test_current_release_opens(self):
        dataset = SharedDataset.open_current(ROOT)
        self.assertEqual(dataset.release_id, "shared-phase6-v1")
        self.assertIn("sensei.canonical_midi", dataset.collection_ids())
        self.assertIn("arrangementgps.legacy_examples", dataset.collection_ids())

    def test_locked_phase6_artifacts_verify(self):
        results = SharedDataset.open_current(ROOT).verify()
        self.assertEqual(results["sensei.canonical_midi"]["status"], "verified")
        self.assertEqual(results["arrangementgps.legacy_examples"]["status"], "skipped_legacy")

    def test_json_and_jsonl_readers_are_typed(self):
        dataset = SharedDataset.open_current(ROOT)
        graph = dataset.read_json("sensei.genre_neighbor_graph")
        self.assertIsInstance(graph, dict)
        first_clip = next(dataset.iter_jsonl("sensei.canonical_midi"))
        self.assertIsInstance(first_clip, dict)
        with self.assertRaises(TypeError):
            dataset.read_json("sensei.canonical_midi")

    def test_unknown_collection_fails(self):
        dataset = SharedDataset.open_current(ROOT)
        with self.assertRaises(KeyError):
            dataset.collection("missing.collection")

    def test_tampered_artifact_fails_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact.json"
            artifact.write_text("{}", encoding="utf-8")
            manifest_dir = root / "releases" / "v1"
            manifest_dir.mkdir(parents=True)
            (root / "releases" / "current.json").write_text(
                json.dumps({"release_id": "v1", "manifest": "v1/manifest.json"}), encoding="utf-8"
            )
            (manifest_dir / "manifest.json").write_text(
                json.dumps({
                    "schema_version": "ai-producer.dataset-release.v1",
                    "release_id": "v1",
                    "collections": {
                        "test": {
                            "path": "../../artifact.json", "format": "json", "status": "locked",
                            "bytes": 2, "sha256": "0" * 64
                        }
                    }
                }),
                encoding="utf-8",
            )
            with self.assertRaises(DatasetIntegrityError):
                SharedDataset.open_current(root).verify()


if __name__ == "__main__":
    unittest.main()
