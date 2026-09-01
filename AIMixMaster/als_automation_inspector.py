#!/usr/bin/env python3
from __future__ import annotations

import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TRACK_TAGS = {"AudioTrack", "MidiTrack", "ReturnTrack", "GroupTrack", "MasterTrack"}
DEVICE_HINT_TAGS = {
    "Eq8", "Compressor2", "GlueCompressor", "DrumBuss", "Saturator",
    "StereoGain", "Utility", "Limiter", "PluginDevice", "AutoPan",
    "Reverb", "HybridReverb", "Delay", "AutoFilter", "MultiBandDynamics",
    "InstrumentGroupDevice", "AudioEffectGroupDevice", "MidiEffectGroupDevice",
}

def val(node, default=""):
    if node is None:
        return default
    return node.get("Value", node.text or default)

def load_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)

def parent_map(root):
    return {child: parent for parent in root.iter() for child in parent}

def id_map(root):
    return {n.get("Id"): n for n in root.iter() if n.get("Id")}

def ancestors(node, parents):
    out = []
    while node in parents:
        node = parents[node]
        out.append(node)
    return out

def nearest(node, parents, tags):
    for a in ancestors(node, parents):
        if a.tag in tags:
            return a
    return None

def effective_name(node, fallback=""):
    if node is None:
        return fallback
    for path in (".//Name/EffectiveName", ".//Name/UserName", ".//UserName", ".//EffectiveName"):
        found = node.find(path)
        text = val(found, "").strip()
        if text:
            return text
    return node.get("Name", "") or fallback

def device_name(device):
    if device is None:
        return "Mixer"
    if device.tag == "PluginDevice":
        for path in (".//PluginDesc/VstPluginInfo/PlugName", ".//PluginDesc/PluginInfo/Name", ".//PlugName"):
            text = val(device.find(path), "").strip()
            if text:
                return text
    return effective_name(device, device.tag)

def track_name(track):
    return effective_name(track, track.tag) or track.get("Id", track.tag)

def parameter_label(target, parents):
    if target is None:
        return "Unknown parameter"

    parent = parents.get(target)
    grandparent = parents.get(parent) if parent is not None else None

    if target.tag == "Manual" and parent is not None:
        return parent.tag
    if target.tag in {"ParameterValue", "LockEnvelope"} and parent is not None:
        return parent.tag
    if parent is not None and parent.tag.startswith("PluginFloatParameter"):
        return parent.tag
    if grandparent is not None and grandparent.tag.startswith("PluginFloatParameter"):
        return grandparent.tag
    if parent is not None and parent.tag not in {"AutomationTarget", "EnvelopeTarget"}:
        return f"{parent.tag}.{target.tag}"
    return target.tag

def target_kind_owner(target, parents):
    if target is None:
        return "unknown", "Unknown"

    device = nearest(target, parents, DEVICE_HINT_TAGS)
    mixer = nearest(target, parents, {"Mixer"})

    if mixer is not None and device is None:
        return "mixer", "Mixer"
    if device is not None:
        return "device", device_name(device)
    return "xml", "XML"

def envelope_points(env):
    points = env.findall(".//BreakpointEvent")
    if points:
        return points
    return env.findall(".//FloatEvent")

def envelope_target_id(env):
    for path in (
        ".//EnvelopeTarget/PointeeId",
        ".//AutomationTarget/PointeeId",
        ".//AutomationTarget/Id",
        ".//EnvelopeTarget/Id",
    ):
        text = val(env.find(path), "").strip()
        if text:
            return text
    return ""

def point_time(point):
    for key in ("Time", "SecTime", "ValueTime"):
        if point.get(key) is not None:
            return point.get(key)
    for child in ("Time", "SecTime", "ValueTime"):
        text = val(point.find(f"./{child}"), "").strip()
        if text:
            return text
    return "?"

def point_value(point):
    for key in ("Value", "Y", "FloatValue"):
        if point.get(key) is not None:
            return point.get(key)
    for child in ("Value", "Y", "FloatValue"):
        text = val(point.find(f"./{child}"), "").strip()
        if text:
            return text
    return val(point, "?")

def short_points(points):
    if not points:
        return ""
    first = points[0]
    last = points[-1]
    return f"first {point_time(first)}={point_value(first)} | last {point_time(last)}={point_value(last)}"

def collect_automation(path):
    """Otomasyon zarflarini yapisal olarak dondurur.

    main() bunu yazdiriyor; MCP sunucusu da ayni fonksiyonu cagiriyor, boylece
    metin rapor ile arac ciktisi ayni yerden gelir.
    """
    root = load_root(path)
    parents = parent_map(root)
    ids = id_map(root)

    tracks = []
    total = 0
    unresolved = 0

    for track in root.iter():
        if track.tag not in TRACK_TAGS:
            continue

        envelopes = []
        for env in track.findall(".//AutomationEnvelope"):
            points = envelope_points(env)
            if not points:
                continue

            pid = envelope_target_id(env)
            target = ids.get(pid) if pid else None
            if target is None:
                unresolved += 1

            kind, owner = target_kind_owner(target, parents)
            envelopes.append({
                "kind": kind,
                "owner": owner,
                "parameter": parameter_label(target, parents),
                "pointee_id": pid or None,
                "point_count": len(points),
                "first_time": point_time(points[0]),
                "first_value": point_value(points[0]),
                "last_time": point_time(points[-1]),
                "last_value": point_value(points[-1]),
                "resolved": target is not None,
            })

        if envelopes:
            total += len(envelopes)
            tracks.append({"track": track_name(track), "envelopes": envelopes})

    return {
        "schema_version": "als.automation-view.v1",
        "read_only": True,
        "tracks": tracks,
        "envelope_count": total,
        "unresolved_targets": unresolved,
    }


def main(path):
    report = collect_automation(path)

    print("==== ALS AUTOMATION VIEW v1 ====")
    print("Read-only. PointeeId -> device/parameter resolver. FloatEvent supported.\n")

    for entry in report["tracks"]:
        print(f"TRACK: {entry['track']}")
        for env in entry["envelopes"]:
            sample = f"first {env['first_time']}={env['first_value']} | last {env['last_time']}={env['last_value']}"
            print(f"  - {env['kind'].upper():7} | {env['owner']} | {env['parameter']} | PointeeId:{env['pointee_id'] or '?'} | Points:{env['point_count']} | {sample}")
        print()

    print("==== SUMMARY ====")
    print(f"Automation envelopes: {report['envelope_count']}")
    print(f"Unresolved targets  : {report['unresolved_targets']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 als_automation_inspector.py <project.als>")
    main(sys.argv[1])
