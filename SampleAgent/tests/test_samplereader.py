"""Tests built on signals whose answer is known by construction.

No file from the producer's library is used here: a test that needs a 4 GB
sample folder to run is a test nobody runs.
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from samplereader import build_profile, read_file, score  # noqa: E402
from samplereader.read import _fold_to_chop, _loop_length_tempo  # noqa: E402
from samplereader.profile import MIN_PROFILE_FILES  # noqa: E402

SR = 44100


def click_loop(path: Path, bpm: float, bars: int = 2, sr: int = SR) -> Path:
    """A click on every beat, file length exactly `bars` bars long."""
    beats = bars * 4
    duration = beats * 60.0 / bpm
    y = np.zeros(int(round(duration * sr)), dtype=np.float32)
    click = np.exp(-np.linspace(0, 12, int(sr * 0.02))).astype(np.float32)
    click *= np.sin(2 * np.pi * 1500 * np.arange(click.size) / sr)
    for b in range(beats):
        start = int(round(b * (60.0 / bpm) * sr))
        end = min(start + click.size, y.size)
        y[start:end] += click[: end - start]
    sf.write(path, y * 0.5, sr)
    return path


def tone(path: Path, freq: float, seconds: float, sr: int = SR) -> Path:
    t = np.arange(int(seconds * sr)) / sr
    y = 0.3 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    sf.write(path, y, sr)
    return path


class FoldTest(unittest.TestCase):
    def test_folds_a_fast_record_into_the_chop_window(self):
        self.assertAlmostEqual(_fold_to_chop(160.0), 80.0, places=6)
        self.assertAlmostEqual(_fold_to_chop(45.0), 90.0, places=6)

    def test_leaves_a_tempo_already_in_the_window_alone(self):
        self.assertAlmostEqual(_fold_to_chop(84.0), 84.0, places=6)


class LoopLengthTest(unittest.TestCase):
    def test_two_bars_at_90_is_recovered_from_duration(self):
        bpm, reason = _loop_length_tempo(2 * 4 * 60.0 / 90.0, hint=90.0)
        self.assertIsNone(reason)
        self.assertAlmostEqual(bpm, 90.0, places=1)

    def test_the_hint_chooses_the_octave(self):
        duration = 4 * 60.0 / 90.0  # one bar at 90, or two bars at 180
        self.assertAlmostEqual(_loop_length_tempo(duration, hint=90.0)[0], 90.0, places=1)
        self.assertAlmostEqual(_loop_length_tempo(duration, hint=180.0)[0], 180.0, places=1)

    def test_refuses_when_no_bar_count_gives_a_plausible_tempo(self):
        bpm, reason = _loop_length_tempo(0.4, hint=90.0)
        self.assertIsNone(bpm)
        self.assertIn("no_plausible_bar_count", reason)


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_reads_container_facts_off_the_header(self):
        r = read_file(tone(self.dir / "tone.wav", 440.0, 3.0))
        self.assertTrue(r.ok)
        self.assertEqual(r.sample_rate, SR)
        self.assertEqual(r.channels, 1)
        self.assertAlmostEqual(r.duration_s, 3.0, places=2)

    def test_recovers_the_tempo_of_a_constructed_loop(self):
        r = read_file(click_loop(self.dir / "click.wav", 86.0))
        self.assertTrue(r.ok)
        self.assertEqual(r.tempo_source, "loop_length")
        self.assertLess(abs(r.tempo_bpm - 86.0) / 86.0, 0.03)
        self.assertTrue(r.in_chop_range)

    def test_a_fast_loop_folds_into_the_chop_window(self):
        r = read_file(click_loop(self.dir / "fast.wav", 172.0))
        self.assertTrue(r.ok)
        self.assertLess(abs(r.chop_bpm - 86.0) / 86.0, 0.03)
        self.assertTrue(r.in_chop_range)

    def test_a_one_shot_gets_no_tempo_and_says_why(self):
        r = read_file(tone(self.dir / "hit.wav", 200.0, 0.3))
        self.assertTrue(r.ok)
        self.assertIsNone(r.tempo_bpm)
        self.assertIn("one_shot", r.tempo_reason)

    def test_a_bright_tone_measures_brighter_than_a_dark_one(self):
        dark = read_file(tone(self.dir / "dark.wav", 200.0, 3.0))
        bright = read_file(tone(self.dir / "bright.wav", 6000.0, 3.0))
        self.assertLess(dark.centroid_hz, bright.centroid_hz)

    def test_a_missing_file_fails_without_raising(self):
        r = read_file(self.dir / "nope.wav")
        self.assertFalse(r.ok)
        self.assertIn("header", r.error)

    def test_digital_silence_is_reported_apart_from_the_noise_floor(self):
        y = np.zeros(SR * 3, dtype=np.float32)
        y[: SR] = 0.2 * np.sin(2 * np.pi * 300 * np.arange(SR) / SR)
        path = self.dir / "gap.wav"
        sf.write(path, y, SR)
        r = read_file(path)
        self.assertTrue(r.ok)
        self.assertGreater(r.silence_share, 0.5)
        self.assertIsNotNone(r.noise_floor_dbfs)
        self.assertGreater(r.noise_floor_dbfs, -200.0)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _readings(self, count):
        return [
            read_file(click_loop(self.dir / f"c{i}.wav", 80.0 + i))
            for i in range(count)
        ]

    def test_refuses_to_build_on_thin_evidence(self):
        profile = build_profile(self._readings(MIN_PROFILE_FILES - 1), "thin")
        self.assertFalse(profile["ok"])
        self.assertIn("thin_evidence", profile["reason"])

    def test_builds_once_there_are_enough_files(self):
        profile = build_profile(self._readings(MIN_PROFILE_FILES + 2), "enough")
        self.assertTrue(profile["ok"])
        self.assertIn("centroid_hz", profile["bands"])
        self.assertGreater(profile["tempo"]["n"], 0)

    def test_a_file_from_the_profile_scores_closer_than_a_stranger(self):
        readings = self._readings(MIN_PROFILE_FILES + 2)
        profile = build_profile(readings, "enough")
        near = score(readings[0], profile)
        far = score(read_file(tone(self.dir / "stranger.wav", 7000.0, 6.0)), profile)
        self.assertTrue(near.scored)
        self.assertTrue(far.scored)
        self.assertLess(near.distance, far.distance)

    def test_scoring_against_an_unusable_profile_is_refused_not_guessed(self):
        thin = build_profile(self._readings(2), "thin")
        result = score(read_file(click_loop(self.dir / "x.wav", 84.0)), thin)
        self.assertFalse(result.scored)
        self.assertEqual(result.reason, "profile_not_usable")

class QualityTest(unittest.TestCase):
    """Kalibrasyondan gelen esikler: 32 kbps elenmeli, orijinal gecmeli."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _noise(self, path, seconds=12.0, cutoff_hz=None):
        """Duz gurultu; cutoff verilirse KESKIN duvar.

        Ilk deneme butter(12) alcak geciren kullandi ve 500 Hz'de yalnizca
        12.4 dB dustu -- test hakliydi, sentetik sinyal yeterince dik degildi.
        Kodek gercekte spektrumu sifirlar, o yuzden burada da FFT'de sifirlaniyor.
        """
        rng = np.random.default_rng(7)
        y = rng.normal(0, 0.15, int(seconds * 44100)).astype(np.float32)
        if cutoff_hz:
            spec = np.fft.rfft(y)
            freqs = np.fft.rfftfreq(len(y), 1 / 44100)
            spec[freqs > cutoff_hz] = 0.0
            y = np.fft.irfft(spec, len(y)).astype(np.float32)
            y *= 0.15 / max(float(np.sqrt(np.mean(y ** 2))), 1e-9)
        sf.write(path, y, 44100)
        return path

    def test_a_brickwall_cut_reads_as_a_codec_cliff(self):
        from samplereader.quality import measure
        q = measure(self._noise(self.dir / "cut.wav", cutoff_hz=5000))
        self.assertTrue(q.ok)
        self.assertGreater(q.cliff_db, 35.0)
        self.assertTrue(q.verdict.startswith("elendi"))

    def test_full_band_material_has_no_cliff_and_passes(self):
        from samplereader.quality import measure
        q = measure(self._noise(self.dir / "wide.wav"))
        self.assertTrue(q.ok)
        self.assertEqual(q.verdict, "gecti")

    def test_a_silent_file_is_rejected_not_scored(self):
        from samplereader.quality import measure
        sf.write(self.dir / "quiet.wav",
                 np.zeros(44100 * 12, dtype=np.float32), 44100)
        q = measure(self.dir / "quiet.wav")
        self.assertTrue(q.ok)
        self.assertTrue(q.verdict.startswith("elendi"))

    def test_missing_file_fails_without_raising(self):
        from samplereader.quality import measure
        q = measure(self.dir / "nope.wav")
        self.assertFalse(q.ok)
        self.assertIsNotNone(q.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
