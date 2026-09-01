import gzip
import sys
import xml.etree.ElementTree as ET

from aimixmaster.project_analyzer import direct_devices

als = sys.argv[1]

def val(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

DEVICE_TAGS = {
    "Eq8": "EQ Eight",
    "Compressor2": "Compressor",
    "GlueCompressor": "Glue Compressor",
    "StereoGain": "Utility",
    "Limiter": "Limiter",
    "AutoFilter": "Auto Filter",
    "AutoPan": "Auto Pan",
    "Saturator": "Saturator",
    "HybridReverb": "Hybrid Reverb",
    "Reverb": "Reverb",
    "Delay": "Delay",
    "PingPongDelay": "Ping Pong Delay",
    "Echo": "Echo",
    "Utility": "Utility",
    "Gate": "Gate",
    "Redux": "Redux",
    "DrumGroupDevice": "Drum Rack",
    "InstrumentGroupDevice": "Instrument Rack",
    "PluginDevice": "Plugin",
    "OriginalSimpler": "Simpler",
    "MultiSampler": "Sampler",
    "UltraAnalog": "Ultra Analog",
    "Operator": "Operator",
    "Collision": "Collision",
    "Corpus": "Corpus",
    "Impulse": "Impulse",
}

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== DEVICE CHAIN SUMMARY ====")

for track in root.iter():
    if track.tag not in (
        "AudioTrack",
        "MidiTrack",
        "GroupTrack",
        "ReturnTrack",
        "MainTrack",
    ):
        continue

    name = val(track.find("./Name/UserName"), "").strip()
    if not name:
        name = f"({track.tag})"

    ordered = [DEVICE_TAGS.get(device.tag, device.tag) for device in direct_devices(track)]

    print(f"\n[{track.tag}] {name}")

    if ordered:
        for i, d in enumerate(ordered, 1):
            print(f"  {i}. {d}")
    else:
        print("  (No detected devices)")
