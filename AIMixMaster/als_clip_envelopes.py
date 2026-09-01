import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]
target = sys.argv[2].lower() if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""
full = "--full" in sys.argv

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS CLIP ENVELOPES ====")
print("Mode:", "FULL" if full else "SUMMARY")

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

def clip_name(clip):
    n = clip.find("./Name")
    return v(n, "AudioClip")

for track in root.iter("AudioTrack"):
    tn = track.find("./Name/UserName")
    track_name = v(tn, "(unnamed)")

    if target and target not in track_name.lower():
        continue

    printed_track = False

    for ci, clip in enumerate(track.iter("AudioClip"), 1):
        start = v(clip.find("./CurrentStart"), "?")
        end = v(clip.find("./CurrentEnd"), "?")

        disabled = clip.find("./Disabled")
        state = "MUTED" if disabled is not None and v(disabled) == "true" else "ON"

        envelopes = []

        for env in clip.findall(".//Envelope"):
            pname = "UNKNOWN"

            # Parametre adı yakalama denemeleri
            for path in [
                ".//AutomationTarget/LockEnvelope",
                ".//AutomationTarget/Id",
                ".//PointeeId",
                ".//Name",
                ".//UserName",
            ]:
                n = env.find(path)
                if n is not None and v(n):
                    pname = f"{n.tag}:{v(n)}"
                    break

            points = []
            for bp in env.findall(".//BreakpointEvent"):
                time = v(bp.find("./Time"), v(bp, "?"))
                value = v(bp.find("./Value"), "")
                curve = v(bp.find("./CurveControl1X"), "")
                points.append((time, value, curve))

            if points:
                envelopes.append((pname, points))

        # Ableton bazı clip envelope datalarını AutomationEnvelopes altında tutabilir
        for auto in clip.findall(".//AutomationEnvelope"):
            pname = "UNKNOWN_AUTOMATION"

            for path in [
                ".//EnvelopeTarget/LockEnvelope",
                ".//AutomationTarget/LockEnvelope",
                ".//PointeeId",
                ".//Name",
                ".//UserName",
            ]:
                n = auto.find(path)
                if n is not None and v(n):
                    pname = f"{n.tag}:{v(n)}"
                    break

            points = []
            for bp in auto.findall(".//BreakpointEvent"):
                time = v(bp.find("./Time"), v(bp, "?"))
                value = v(bp.find("./Value"), "")
                curve = v(bp.find("./CurveControl1X"), "")
                points.append((time, value, curve))

            if points:
                envelopes.append((pname, points))

        if not envelopes:
            continue

        if not printed_track:
            print(f"\nTRACK: {track_name}")
            printed_track = True

        print(f"  C{ci:03d} | {state:5} | {float(start):7.2f}–{float(end):7.2f} | Envelopes:{len(envelopes)}")

        for pname, points in envelopes:
            print(f"    - {pname} | Points:{len(points)}")
            if full:
                for t, val, curve in points[:80]:
                    extra = f" Curve:{curve}" if curve else ""
                    print(f"        {t} -> {val}{extra}")
                if len(points) > 80:
                    print(f"        ... +{len(points)-80} more")
