from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np
import soundfile as sf

from aimixmaster.gain_staging_v2 import (
    CONTENT_DEPENDENT, DETERMINISTIC_GAIN, UNKNOWN, analyze_gain_staging_v2, classify_device,
)


def track(name: str, audio: Path, devices: list[str]) -> ET.Element:
    item = ET.fromstring(f'''<AudioTrack Id="1"><Name><UserName Value="{name}" /></Name>
    <DeviceChain><Mixer><Volume><Manual Value="1" /></Volume></Mixer><DeviceChain><Devices /></DeviceChain>
    <AudioClip><SampleRef><FileRef><Path Value="{audio}" /></FileRef></SampleRef><SampleVolume Value="1" /></AudioClip>
    </DeviceChain></AudioTrack>''')
    holder = item.find("./DeviceChain/DeviceChain/Devices")
    for tag in devices:
        holder.append(ET.Element(tag))
    return item


class GainStagingV2Test(unittest.TestCase):
    def test_device_classes_are_exact_and_safe(self) -> None:
        self.assertEqual(classify_device(ET.Element("StereoGain")), DETERMINISTIC_GAIN)
        self.assertEqual(classify_device(ET.Element("Compressor2")), CONTENT_DEPENDENT)
        self.assertEqual(classify_device(ET.Element("MyLimiterLikeVst")), UNKNOWN)

    def test_deterministic_source_gets_fader_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "kick.wav"
            sf.write(audio, np.array([[0.5], [-0.5]], dtype=np.float32), 48000)
            root = ET.Element("LiveSet"); root.append(track("KICK", audio, []))
            row = analyze_gain_staging_v2(root, audio.with_suffix(".als"))["tracks"][0]
        self.assertFalse(row["measurement_required"])
        self.assertEqual(row["confidence"], "HIGH")
        self.assertAlmostEqual(row["estimated_prefader_peak"], -6.0206, places=3)
        self.assertAlmostEqual(row["recommended_fader_db"], -1.9794, places=3)

    def test_medium_risk_content_is_estimated_but_unknown_requires_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "source.wav"
            sf.write(audio, np.array([[0.5]], dtype=np.float32), 48000)
            root = ET.Element("LiveSet"); root.append(track("KICK", audio, ["Compressor2"]))
            estimated = analyze_gain_staging_v2(root, audio.with_suffix(".als"))["tracks"][0]
            self.assertFalse(estimated["measurement_required"])
            self.assertEqual(estimated["decision"], "ESTIMATED")
            root = ET.Element("LiveSet"); root.append(track("KICK", audio, ["ThirdPartyVST"]))
            required = analyze_gain_staging_v2(root, audio.with_suffix(".als"))["tracks"][0]
            self.assertTrue(required["measurement_required"])
            self.assertEqual(required["confidence"], "LOW")
            self.assertIsNone(required["recommended_fader_db"])

    def test_source_metrics_include_crest_dc_and_silence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "source.wav"
            sf.write(audio, np.array([[0.5], [0.0]], dtype=np.float32), 48000)
            root = ET.Element("LiveSet"); root.append(track("KICK", audio, []))
            row = analyze_gain_staging_v2(root, audio.with_suffix(".als"))["tracks"][0]
        self.assertIsNotNone(row["crest_factor_db"])
        self.assertAlmostEqual(row["dc_offset"], 0.25, places=5)
        self.assertEqual(row["silence_ratio"], 0.5)
