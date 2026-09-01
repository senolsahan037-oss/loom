from pathlib import Path
import gzip
import xml.etree.ElementTree as ET


def read_ableton_xml(path: str | Path) -> ET.Element:
    path = Path(path)
    raw = path.read_bytes()

    try:
        xml_bytes = gzip.decompress(raw)
    except OSError:
        xml_bytes = raw

    return ET.fromstring(xml_bytes)


def inspect_alc(path: str | Path) -> dict:
    root = read_ableton_xml(path)

    tags = {}
    for elem in root.iter():
        tags[elem.tag] = tags.get(elem.tag, 0) + 1

    return {
        "file": str(path),
        "root_tag": root.tag,
        "tag_counts": dict(sorted(tags.items())),
    }


def _value(elem):
    if elem is None:
        return None
    if "Value" in elem.attrib:
        return elem.attrib.get("Value")
    if elem.text and elem.text.strip():
        return elem.text.strip()
    return None


def _first_value(parent, names):
    for name in names:
        found = parent.find(f".//{name}")
        val = _value(found)
        if val not in (None, ""):
            return val
    return None


def extract_drum_pads(path: str | Path) -> list[dict]:
    root = read_ableton_xml(path)
    pads = []

    for branch in root.iter():
        if branch.tag not in {"DrumBranch", "DrumPad"}:
            continue

        receiving = _first_value(branch, ["ReceivingNote", "ReceiveNote"])
        sending = _first_value(branch, ["SendingNote", "SendNote"])

        note_raw = receiving or sending
        if note_raw is None:
            continue

        try:
            note = int(float(note_raw))
        except ValueError:
            continue

        sample_names = []
        for part in branch.iter("MultiSamplePart"):
            sample_name = _first_value(part, ["Name", "UserName"])
            if sample_name:
                sample_names.append(sample_name)

        branch_name = _first_value(branch, ["UserName", "Name", "Annotation", "ShortName"])

        label = branch_name or (sample_names[0] if sample_names else f"Note {note}")

        pads.append({
            "note": note,
            "label": label,
            "receiving_note": receiving,
            "sending_note": sending,
            "sample_names": sample_names,
            "source_tag": branch.tag,
        })

    by_note = {}
    for pad in pads:
        note = pad["note"]
        if note not in by_note:
            by_note[note] = pad
        elif by_note[note]["label"].startswith("Note ") and not pad["label"].startswith("Note "):
            by_note[note] = pad

    return [by_note[n] for n in sorted(by_note)]

def inspect_drum_structure(path: str | Path, limit: int = 20) -> dict:
    root = read_ableton_xml(path)

    items = []
    for elem in root.iter():
        if elem.tag in {"DrumBranch", "DrumPad", "MultiSamplePart", "SampleRef", "SourceContext", "Name", "UserName", "ReceivingNote", "SendingNote"}:
            items.append({
                "tag": elem.tag,
                "attrib": dict(elem.attrib),
                "text": (elem.text or "").strip()[:120],
                "child_tags": [child.tag for child in list(elem)[:12]],
            })
            if len(items) >= limit:
                break

    return {
        "file": str(path),
        "items": items,
    }


def extract_drum_pads_loose(path: str | Path) -> list[dict]:
    root = read_ableton_xml(path)
    pads = []

    def names_from_context(context):
        names = []
        for part in context.iter("MultiSamplePart"):
            sample_name = _first_value(part, ["Name", "UserName"])
            if sample_name:
                names.append(sample_name)
        return names

    def walk(elem, ancestors):
        if elem.tag == "ReceivingNote":
            receiving = _value(elem)

            contexts = list(reversed(ancestors[-8:]))
            best_context = ancestors[-1] if ancestors else elem
            sample_names = []

            for ctx in contexts:
                found = names_from_context(ctx)
                if found:
                    best_context = ctx
                    sample_names = found
                    break

            sending = _first_value(best_context, ["SendingNote", "SendNote"])

            try:
                note = int(float(receiving))
            except (TypeError, ValueError):
                return

            pads.append({
                "note": note,
                "label": sample_names[0] if sample_names else f"Note {note}",
                "receiving_note": receiving,
                "sending_note": sending,
                "sample_names": sample_names,
                "source_tag": best_context.tag,
            })

        for child in elem:
            walk(child, ancestors + [elem])

    walk(root, [])

    by_note = {}
    for pad in pads:
        note = pad["note"]
        if note not in by_note:
            by_note[note] = pad
        elif by_note[note]["label"].startswith("Note ") and not pad["label"].startswith("Note "):
            by_note[note] = pad

    return [by_note[n] for n in sorted(by_note)]
