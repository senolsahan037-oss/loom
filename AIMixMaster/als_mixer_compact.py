import glob, gzip, math, os, re, sys
import soundfile as sf
import xml.etree.ElementTree as ET

args = sys.argv[1:]
REPORT_PAGE = "all"

if "--page" in args:
    page_index = args.index("--page")
    try:
        REPORT_PAGE = args[page_index + 1].strip().lower()
    except Exception:
        raise SystemExit("Usage: python3 als_mixer_compact.py <project.als> [--page all|console|devices]")

    del args[page_index:page_index + 2]

if not args:
    raise SystemExit("Usage: python3 als_mixer_compact.py <project.als> [--page all|console|devices]")

als = args[0]
als_dir = os.path.dirname(os.path.abspath(als))

LIVE_METERS = {}


def val(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default


def lin_to_db(v):
    try:
        v = float(v)
        if v <= 0:
            return -999.0
        return 20 * math.log10(v)
    except Exception:
        return 0.0


def dbfs(v):
    if v <= 0:
        return None
    return 20 * math.log10(v)


def center(p):
    try:
        return abs(float(p)) < 0.001
    except Exception:
        return True


def clean_track_name(name):
    name = name.strip()

    # ALS sometimes keeps internal/user labels like "# New Old Sub" while
    # Ableton's mixer display shows "New Old Sub". Do not invent channel
    # numbers or symbols; show the mixer-facing name as cleanly as possible.
    if name.startswith("# "):
        name = name[2:].strip()

    return name


def track_name_parts(track):
    effective_name = clean_track_name(val(track.find("./Name/EffectiveName"), ""))
    user_name = clean_track_name(val(track.find("./Name/UserName"), ""))
    return user_name, effective_name


def track_name(track):
    user_name, effective_name = track_name_parts(track)

    # Prefer the user-facing mixer name. EffectiveName can be generated/inherited
    # and may hide paired tracks like kick/sub under the same apparent name.
    return user_name or effective_name or "(unnamed)"


def track_type(track):
    if track.tag == "AudioTrack":
        return "AUDIO"
    if track.tag == "MidiTrack":
        return "MIDI"
    if track.tag == "GroupTrack":
        return "BUS"
    if track.tag == "ReturnTrack":
        return "RETURN"
    return track.tag.upper()


def fmt_db(v):
    if v <= -998:
        return "-inf"
    return f"{v:.1f}dB"


def fmt_peak(v):
    return f"{v:.1f}dBFS" if v is not None else "-"

def fmt_raw_value(value):
    try:
        value_f = float(value)
    except Exception:
        return str(value)

    if abs(value_f) >= 1000:
        return f"{value_f:.0f}"

    if abs(value_f) >= 100:
        return f"{value_f:.1f}".rstrip("0").rstrip(".")

    if abs(value_f) >= 10:
        return f"{value_f:.2f}".rstrip("0").rstrip(".")

    if abs(value_f) >= 1:
        return f"{value_f:.3f}".rstrip("0").rstrip(".")

    return f"{value_f:.4f}".rstrip("0").rstrip(".")


def raw_float(value):
    try:
        return float(value)
    except Exception:
        return None


def linear_gain_to_db_text(value):
    value_f = raw_float(value)
    if value_f is None or value_f <= 0:
        return "-inf dB"
    return f"{20 * math.log10(value_f):.1f} dB"


def percent_text(value):
    value_f = raw_float(value)
    if value_f is None:
        return str(value)
    return f"{value_f * 100:.1f}".rstrip("0").rstrip(".") + "%"


def classify_param_value(label, path, raw_value, device_name=""):
    label_l = (label or "").lower()
    path_l = (path or "").lower()
    device_l = (device_name or "").lower()
    key = f"{path_l}.{label_l}"
    raw_text = fmt_raw_value(raw_value)
    value_f = raw_float(raw_value)

    # Device-specific overrides first. Generic matching is too dumb for ALS,
    # because the same label can mean different things on different devices.
    if "glue compressor" in device_l:
        if label_l in ["attack", "release"]:
            return "enum", f"enum:{raw_text}"
        if label_l == "range":
            return "db", f"{raw_text} dB"
        if label_l in ["threshold", "makeup"]:
            return "db", f"{raw_text} dB"
        if "sidechaineq" in key and (label_l == "mode" or "mode" in key):
            return "enum", f"enum:{raw_text}"

    if device_l == "compressor" or "compressor" in device_l:
        if label_l in ["attack", "release"]:
            return "time_or_enum", raw_text
        if label_l in ["threshold", "makeup", "knee"]:
            return "db", f"{raw_text} dB"
        if "sidechaineq_mode" in key or "sidechaineq.mode" in key:
            return "enum", f"enum:{raw_text}"
        if "sidechaineq_q" in key or "sidechaineq.q" in key:
            return "ratio", raw_text

    if "reverb" in device_l:
        if label_l == "predelay":
            return "ms", f"{raw_text} ms"
        if label_l == "diffusedelay":
            return "seconds", f"{raw_text} s"
        if label_l in ["shelfhigain", "shelflogain", "sizemoddepth", "allpassgain", "allpasssize", "sizesmoothing", "stereoseparation", "roomsize"]:
            return "control_value", raw_text
        if label_l == "decaytime":
            return "time_control", raw_text
        if label_l == "earlyreflectmoddepth":
            return "control_value", raw_text

    if "dynamic tube" in device_l:
        if label_l in ["autobiasattack", "autobiasrelease"]:
            return "time_control", raw_text
        if label_l in ["bias"]:
            return "control_value", raw_text

    if "vinyl distortion" in device_l:
        if label_l in ["cracledensity", "craclevolume"]:
            return "control_value", raw_text

    if "delay" in device_l:
        if "simpledelaytime" in key or "msdelay" in key:
            return "ms", f"{raw_text} ms"
        if "delayline_time" in key:
            if value_f is not None and value_f < 1:
                return "seconds", f"{value_f * 1000:.1f} ms"
            return "seconds", f"{raw_text} s"
        if "syncedsixteenth" in key or "beatdelayenum" in key:
            return "enum", f"enum:{raw_text}"

    if "auto pan" in device_l:
        if "timemode" in key or "sixteenth" in key:
            return "enum", f"enum:{raw_text}"
        if "modulation_time" in key:
            return "ms_or_sync", raw_text

    if "redux" in device_l:
        if label_l == "bitdepth":
            return "bits", f"{raw_text} bit"
        if label_l == "jitter":
            return "percent", percent_text(raw_value)

    if "resonators" in device_l:
        if label_l in ["resdecay"]:
            return "decay", raw_text
        if label_l in ["rescolor"]:
            return "percent_like", f"{raw_text}%"

    # Generic fallback layer. Keep this conservative.
    if label_l in ["mode", "timemode"] or any(token in key for token in [".mode", "_mode", "sixteenth", "syncedsixteenth", "enum"]):
        return "enum", f"enum:{raw_text}"

    if any(token in key for token in ["freq", "frequency", "shelfhifreq", "shelflofreq", "bandfreq", "dampingfrequency", "boomfrequency", "samplerate"]):
        return "hz", f"{raw_text} Hz"

    if any(token in key for token in ["threshold", "makeup", "predrive", "basedrive", "drive", "amplitude"]):
        return "db", f"{raw_text} dB"

    if "bandwidth" in key:
        return "ratio", raw_text

    if any(token in key for token in ["drywet", "amount", "feedback", "crunch", "transient", "density", "volume", "bias"]):
        return "percent", percent_text(raw_value)

    if label_l in ["width", "colorwidth"] or key.endswith(".width") or key.endswith(".colorwidth"):
        return "percent", percent_text(raw_value)

    if label_l == "gain" or key.endswith(".gain"):
        if value_f is not None and 0 < value_f <= 4:
            return "linear_gain", linear_gain_to_db_text(raw_value)
        return "db", f"{raw_text} dB"

    if label_l in ["q", "bandq"] or key.endswith(".q") or key.endswith("_q") or key.endswith(".bandq") or any(token in key for token in ["ratio", "expansionratio"]):
        return "ratio", raw_text

    if label_l in ["phase", "modulation_phase"] or key.endswith(".phase") or key.endswith("_phase"):
        return "degree", f"{raw_text}°"

    if any(token in key for token in ["attack", "release"]):
        return "time_or_enum", raw_text

    if any(token in key for token in ["predelay", "delay", "time", "msdelay", "simpledelaytime"]):
        return "time", raw_text

    if any(token in key for token in ["pitch", "note"]):
        return "semitone_or_note", raw_text

    return "raw", raw_text

def pan_label(pan_raw):
    try:
        pan = float(pan_raw)
    except Exception:
        return "C"

    if abs(pan) < 0.001:
        return "C"

    side = "R" if pan > 0 else "L"
    return f"{side}{abs(pan):.3f}"


def send_values(track):
    sends = []

    # Ableton stores send values in TrackSendHolder blocks.
    for index, send in enumerate(track.findall(".//Sends/TrackSendHolder/Send/Manual")):
        label = chr(ord("A") + index)
        send_db = lin_to_db(val(send, "0"))

        # Ableton uses very low values around -70 dB as practical silence/off.
        # Do not print closed sends; they only clutter the mixer view.
        if send_db <= -60.0:
            continue

        sends.append(f"{label}:{fmt_db(send_db)}")

    if sends:
        return " ".join(sends)

    return "-"


def resolve_audio_path(path):
    if not path:
        return ""

    candidates = [
        path,
        os.path.join(als_dir, path),
        os.path.join(os.path.dirname(als_dir), path),
        os.path.join(als_dir, "Samples", "Imported", os.path.basename(path)),
        os.path.join(als_dir, "Samples", "Processed", "Crop", os.path.basename(path)),
        os.path.join(als_dir, "Samples", "Processed", "Freeze", os.path.basename(path)),
        os.path.join(als_dir, "Samples", "Processed", "Bounce", os.path.basename(path)),
    ]

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return path


def first_audio_clip_path(track):
    for clip in track.iter("AudioClip"):
        disabled = clip.find("./Disabled")
        if disabled is not None and val(disabled).lower() == "true":
            continue

        path_node = clip.find(".//SampleRef/FileRef/Path")
        rel_node = clip.find(".//SampleRef/FileRef/RelativePath")
        source = val(path_node, "") or val(rel_node, "")

        if source.lower().endswith((".wav", ".aif", ".aiff")):
            return resolve_audio_path(source)

    return ""


def audio_peak_dbfs(path, max_frames=441000):
    if not path or not os.path.exists(path):
        return None

    try:
        data, _ = sf.read(path, frames=max_frames, always_2d=True)
        if data.size == 0:
            return None
        return dbfs(float(abs(data).max()))
    except Exception:
        return None


def manual_value(node):
    manual = node.find("./Manual")
    if manual is None:
        return None
    return manual.attrib.get("Value")


def default_value(node):
    default = node.find("./DefaultValue")
    if default is None:
        return None
    return default.attrib.get("Value")


def param_label(param):
    for query in ["./Name/UserName", "./Name/EffectiveName", "./Name"]:
        node = param.find(query)
        text = val(node, "").strip()
        if text:
            return clean_device_name(text)

    return clean_device_name(param.tag)


def param_default_value(param):
    for query in ["./DefaultValue", "./Default", "./Default/Value"]:
        node = param.find(query)
        text = val(node, "").strip()
        if text:
            return text

    for attr in ["DefaultValue", "Default", "default"]:
        if attr in param.attrib:
            return param.attrib.get(attr)

    return None


def is_neutral_manual_value(value):
    try:
        value_f = float(value)
    except Exception:
        return value in ["False", "false", "0"]

    return abs(value_f) < 0.000001 or abs(value_f - 1.0) < 0.000001


def almost_equal(a, b, eps=0.0001):
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return False


def is_known_device_default(device_name, label, path, value):
    device_l = (device_name or "").lower()
    label_l = (label or "").lower()
    path_l = (path or "").lower()

    # Fallback-only cleanup: common Ableton stock device state values
    # that often appear without DefaultValue in ALS.
    if "eq eight" in device_l:
        if label_l == "q" and almost_equal(value, 0.7071):
            return True
        if label_l == "freq" and almost_equal(value, 40):
            return True
        if label_l == "freq" and almost_equal(value, 200) and "bands.1" in path_l:
            return True
        if label_l == "mode" and almost_equal(value, 2):
            return True
        if label_l == "mode" and almost_equal(value, 3) and "bands.1" in path_l:
            return True

    if "reverb" in device_l:
        known = {
            "predelay": 2.5,
            "bandfreq": 830,
            "bandwidth": 5.85,
            "earlyreflectmodfreq": 0.2977,
            "earlyreflectmoddepth": 17.5,
            "diffusedelay": 0.5,
            "shelfhifreq": 4500,
            "shelfhigain": 0.7,
            "shelflofreq": 90,
            "shelflogain": 0.75,
            "sizemodfreq": 0.02,
            "sizemoddepth": 0.02,
            "decaytime": 1200,
            "allpassgain": 0.6,
            "allpasssize": 0.4,
            "roomsize": 100,
            "sizesmoothing": 2,
            "stereoseparation": 100,
        }
        if label_l in known and almost_equal(value, known[label_l]):
            return True

    if "saturator" in device_l:
        known = {
            "colorfrequency": 1000,
            "colorwidth": 0.3,
            "bassshaperthreshold": -50,
            "wslin": 0.5,
            "wscurve": 0.05,
        }
        if label_l in known and almost_equal(value, known[label_l]):
            return True

    if "auto pan" in device_l:
        known = {
            "modulation_frequency": 0.1,
            "modulation_sixteenth": 16,
            "modulation_phase": 180,
        }
        if label_l in known and almost_equal(value, known[label_l]):
            return True

    if "glue compressor" in device_l:
        known = {
            "range": 70,
            "attack": 3,
            "release": 3,
            "sidechaineq.mode": 5,
            "sidechaineq.freq": 200,
            "sidechaineq.q": 0.7071,
        }
        for key, default_value in known.items():
            if (label_l == key or key in path_l) and almost_equal(value, default_value):
                return True

    return False


# === New helpers for param output ===
def compact_xml_path(device, node):
    parent_by_id = {}

    for parent in device.iter():
        for child in list(parent):
            parent_by_id[id(child)] = parent

    parts = []
    current = node

    while current is not None and current is not device:
        parent = parent_by_id.get(id(current))
        tag = clean_device_name(current.tag) or current.tag

        if parent is not None:
            same_tag_siblings = [child for child in list(parent) if child.tag == current.tag]
            if len(same_tag_siblings) > 1:
                index = same_tag_siblings.index(current)
                tag = f"{tag}[{index}]"

        parts.append(tag)
        current = parent

    return ".".join(reversed(parts)) if parts else clean_device_name(node.tag) or node.tag


def format_param_identity(param):
    if isinstance(param, dict):
        return f"{param.get('path', '')}|{param.get('label', '')}|{param.get('raw_value', param.get('value', ''))}"
    return str(param)


def format_param_inline(param):
    if isinstance(param, dict):
        label = param.get("label", "")
        raw_value = param.get("raw_value", param.get("value", ""))
        display = param.get("display", raw_value)
        path = param.get("path", "")
        if path and label:
            return f"{path} {label}={display} raw={raw_value}"
        if label:
            return f"{label}={display} raw={raw_value}"
        return f"raw={raw_value}"
    return str(param)


def moved_params(device, limit=8):
    strict_params = []
    fallback_params = []
    seen = set()
    device_name = clean_device_name(device_label(device))

    ignored_param_names = {
        "Manual",
        "DefaultValue",
        "AutomationTarget",
        "MidiControllerRange",
        "ModulationTarget",
        "LomId",
        "LomIdView",
    }

    for param in device.iter():
        manual = manual_value(param)
        if manual is None:
            continue

        label = param_label(param)
        if not label or label in ignored_param_names:
            continue

        try:
            manual_f = float(manual)
        except Exception:
            continue

        default = default_value(param) or param_default_value(param)
        path = compact_xml_path(device, param)

        if default is not None:
            try:
                default_f = float(default)
            except Exception:
                continue

            if abs(manual_f - default_f) < 0.000001:
                continue

            value_type, display_value = classify_param_value(label, path, manual_f, device_name)
            item = {
                "path": path,
                "label": label,
                "raw_value": fmt_raw_value(manual_f),
                "value_type": value_type,
                "display": display_value,
            }
            identity = format_param_identity(item)
            if identity not in seen:
                strict_params.append(item)
                seen.add(identity)
        else:
            if is_neutral_manual_value(manual):
                continue

            if is_known_device_default(device_name, label, path, manual_f):
                continue

            value_type, display_value = classify_param_value(label, path, manual_f, device_name)
            item = {
                "path": path,
                "label": label,
                "raw_value": fmt_raw_value(manual_f),
                "value_type": value_type,
                "display": display_value,
            }
            identity = format_param_identity(item)
            if identity not in seen:
                fallback_params.append(item)
                seen.add(identity)

        if len(strict_params) >= limit:
            break

    params = strict_params if strict_params else fallback_params
    return params[:limit]


def device_label(device):
    user_name = val(device.find("./UserName"), "").strip()
    effective_name = val(device.find("./Name/EffectiveName"), "").strip()
    branch_name = val(device.find(".//AudioEffectBranch/Name/EffectiveName"), "").strip()
    return user_name or effective_name or branch_name or device.tag

def rack_signal_paths(rack):
    paths = []

    for branch in rack.iter("AudioEffectBranch"):
        branch_name = clean_device_name(val(branch.find("./Name/EffectiveName"), ""))
        branch_devices = []

        for device in branch.findall(".//Devices/*"):
            if not is_device_node(device):
                continue

            label = clean_device_name(device_label(device))
            if not label or label in ["DeviceChain", "Devices", "Mixer", branch_name]:
                continue

            if label not in branch_devices:
                branch_devices.append(label)

        if branch_devices:
            chain_text = " | ".join(branch_devices)

            # Ableton auto-generates rack chain display names from the devices
            # when the user does not name the chain manually. Use the displayed
            # project-facing chain name as source of truth; do not print a
            # second interpreted device chain under it.
            if branch_name:
                paths.append(branch_name)
            else:
                paths.append(chain_text)

    return paths


def is_device_node(node):
    ignored = {
        "DeviceChain",
        "Devices",
        "Mixer",
        "BranchSelector",
        "AudioEffectBranch",
        "AudioEffectBranchList",
        "MainSequencer",
        "ClipSlotList",
        "ClipSlot",
        "Name",
        "UserName",
        "EffectiveName",
        "SelectedDevice",
        "FreezeSequencer",
        "SpectrumAnalyzer",
        "MixerDevice",
    }

    if node.tag in ignored:
        return False

    if node.tag.endswith("Device"):
        return True

    if node.find("./UserName") is not None:
        return True

    if node.find("./Name/EffectiveName") is not None and (
        node.find(".//Manual") is not None or node.find(".//Parameters") is not None
    ):
        return True

    return False


def clean_device_name(name):
    aliases = {
        "Eq8": "EQ Eight",
        "Compressor2": "Compressor",
        "GlueCompressor": "Glue Compressor",
        "Tube": "Dynamic Tube",
        "Vinyl": "Vinyl Distortion",
        "Resonator": "Resonators",
        "AutoPan2": "Auto Pan",
        "Redux2": "Redux",
        "StereoGain": "Utility",
    }

    trash = {
        "SelectedDevice",
        "FreezeSequencer",
        "SpectrumAnalyzer",
        "MixerDevice",
    }

    name = name.strip()
    if not name or name in trash:
        return ""
    return aliases.get(name, name)


def device_chain(track):
    device_lines = []
    seen = set()
    skip_nodes = set()
    track_display_name = track_name(track)

    for device in track.iter():
        if device is track:
            continue

        if id(device) in skip_nodes:
            continue

        if not is_device_node(device):
            continue

        if device.tag == "AudioEffectGroupDevice":
            rack_paths = rack_signal_paths(device)
            if rack_paths:
                for child_node in device.iter():
                    if child_node is not device:
                        skip_nodes.add(id(child_node))
                line = "RACK{" + " || ".join(rack_paths) + "}"
            else:
                label = clean_device_name(device_label(device))
                if not label or label in ["DeviceChain", "Devices", "Mixer", track_display_name]:
                    continue
                line = label
        else:
            label = clean_device_name(device_label(device))
            if not label or label in ["DeviceChain", "Devices", "Mixer", track_display_name]:
                continue
            line = label

        key = (device.tag, line)
        if key in seen:
            continue
        seen.add(key)

        device_lines.append(line)

    if not device_lines:
        for dev in track.iter("AudioEffectGroupDevice"):
            chain = val(dev.find(".//AudioEffectBranch/Name/EffectiveName"), "").strip()
            if chain:
                for item in [x.strip() for x in chain.split("|") if x.strip()]:
                    item = clean_device_name(item)
                    if item and item not in device_lines:
                        device_lines.append(item)

    return " > ".join(device_lines) if device_lines else "-"



def device_param_details(track):
    details = []
    seen = set()
    skip_nodes = set()
    track_display_name = track_name(track)

    for rack in track.iter("AudioEffectGroupDevice"):
        for branch in rack.iter("AudioEffectBranch"):
            branch_name = clean_device_name(val(branch.find("./Name/EffectiveName"), ""))

            for device in branch.findall(".//Devices/*"):
                if not is_device_node(device):
                    continue

                skip_nodes.add(id(device))

                label = clean_device_name(device_label(device))
                if not label or label in ["DeviceChain", "Devices", "Mixer", track_display_name, branch_name]:
                    continue

                params = moved_params(device, limit=10)
                if not params:
                    continue

                path = f"RACK > {branch_name} > {label}" if branch_name else f"RACK > {label}"
                key = (path, tuple(format_param_identity(item) for item in params))
                if key in seen:
                    continue
                seen.add(key)

                details.append((path, params))

    for device in track.iter():
        if device is track:
            continue

        if id(device) in skip_nodes:
            continue

        if device.tag == "AudioEffectGroupDevice":
            continue

        if not is_device_node(device):
            continue

        label = clean_device_name(device_label(device))
        if not label or label in ["DeviceChain", "Devices", "Mixer", track_display_name]:
            continue

        params = moved_params(device, limit=10)
        if not params:
            continue

        key = (label, tuple(format_param_identity(item) for item in params))
        if key in seen:
            continue
        seen.add(key)

        details.append((label, params))

    return details



def input_routing_label(track):
    candidates = [
        ".//DeviceChain/Mixer/AudioInputRouting/UpperDisplayString",
        ".//DeviceChain/Mixer/AudioInputRouting/LowerDisplayString",
        ".//DeviceChain/Mixer/AudioInputRouting/Target/Value",
        ".//AudioInputRouting/UpperDisplayString",
        ".//AudioInputRouting/LowerDisplayString",
        ".//AudioInputRouting/Target/Value",
        ".//InputRouting/UpperDisplayString",
        ".//InputRouting/LowerDisplayString",
        ".//InputRouting/Target/Value",
    ]

    found = []
    for query in candidates:
        node = track.find(query)
        text = val(node, "").strip()
        if text and text not in found:
            found.append(text)

    if not found:
        return "-"

    return " > ".join(found)


def routing_label(track):
    candidates = [
        ".//DeviceChain/Mixer/AudioOutputRouting/UpperDisplayString",
        ".//DeviceChain/Mixer/AudioOutputRouting/LowerDisplayString",
        ".//DeviceChain/Mixer/AudioOutputRouting/Target/Value",
        ".//AudioOutputRouting/UpperDisplayString",
        ".//AudioOutputRouting/LowerDisplayString",
        ".//AudioOutputRouting/Target/Value",
        ".//OutputRouting/UpperDisplayString",
        ".//OutputRouting/LowerDisplayString",
        ".//OutputRouting/Target/Value",
    ]

    found = []
    for query in candidates:
        node = track.find(query)
        text = val(node, "").strip()
        if text and text not in found:
            found.append(text)

    if found:
        return " > ".join(found)

    if track.tag == "GroupTrack":
        return "Main"
    if track.tag == "ReturnTrack":
        return "Master"
    if track.tag == "AudioTrack":
        return "Main"
    if track.tag == "MidiTrack":
        return "Main"

    return "UNKNOWN"


def parent_for(rows, index):
    row = rows[index]
    name = row["name"]
    kind = row["type"]

    if kind == "RETURN":
        return "MASTER"

    if name == "Pre-Master":
        return "MASTER"

    if kind == "BUS":
        if name in ["PERC BUSS", "SNARE BUSS", "KİCK BUSS", "KICK BUSS"]:
            return "drum buss"
        return "Pre-Master"

    if kind in ["AUDIO", "MIDI"]:
        for previous in reversed(rows[:index]):
            if previous["type"] == "BUS":
                return previous["name"]
        return "Pre-Master"

    return "Pre-Master"


def meter_bar(out_text, width=12):
    if not out_text or out_text in ["-", "N/A"]:
        return "░" * width

    try:
        db = float(out_text.replace("dBFS", ""))
    except Exception:
        return "░" * width

    filled = int(max(0, min(width, round((db + 30.0) / 30.0 * width))))
    return "█" * filled + "░" * (width - filled)


def fit_col(text, width):
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def rack_paths_from_devices(devices):
    if not devices or "RACK{" not in devices:
        return []

    paths = []
    for content in re.findall(r"RACK\{([^{}]*)\}", devices):
        paths.extend([item.strip() for item in content.split(" || ") if item.strip()])

    return paths


def short_devices(devices):
    if not devices or devices == "-":
        return "-"

    rack_paths = rack_paths_from_devices(devices)
    if not rack_paths:
        return devices

    # Keep the main console readable. Full rack/parallel-path detail is printed
    # in the RACK DETAILS section below.
    without_rack = re.sub(r"\s*>?\s*RACK\{[^{}]*\}\s*>?\s*", " > ", devices).strip(" >")
    summary = f"RACK[{len(rack_paths)} paths]"

    if without_rack:
        return f"{summary} + {without_rack}"
    return summary


def latest_ableton_log_path():
    paths = glob.glob(os.path.expanduser("~/Library/Preferences/Ableton/Live */Log.txt"))
    if not paths:
        return ""
    return max(paths, key=os.path.getmtime)


def load_live_meters(max_lines=30000):
    """
    LIVE column uses MixConsoleLive2 PEAK field.

    In the live terminal stream:
      LIVE = current meter
      PEAK = held highest hit so far

    Console LIVE should show the held peak, because this column is used as
    a stable peak-meter memory, not as an instant meter.
    """
    import re
    from pathlib import Path
    import json

    # Prefer JSON snapshot written by MixConsoleLive2 remote script
    json_path = Path("/tmp/mixconsole_live.json")
    meters = {}
    if json_path.exists():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
            for name, data in (raw.get("tracks") or {}).items():
                clean_name = clean_track_name(name)
                if not clean_name:
                    continue
                # Expect values live/peak/left/right already formatted as strings
                meters[clean_name] = {
                    "live": data.get("live") or data.get("LIVE") or "none",
                    "peak": data.get("peak") or data.get("PEAK") or "none",
                    "left": data.get("left") or data.get("L") or "none",
                    "right": data.get("right") or data.get("R") or "none",
                }
            return meters
        except Exception:
            # Fall back to Log.txt parsing if JSON is unreadable
            meters = {}

    log_path = latest_ableton_log_path()
    if not log_path:
        return meters
    log_path = Path(log_path)

    meter_re = re.compile(
        r"MixConsoleLive2\) (?P<name>.+?) \| "
        r"LIVE:(?P<live>[^|]+) \| PEAK:(?P<peak>[^|]+) \| "
        r"L:(?P<left>[^|]+) \| R:(?P<right>[^|]+)"
    )

    legacy_meter_re = re.compile(
        r"MixConsoleLive2\) (?P<name>.+?) \| "
        r"LIVE:(?P<live>[^|]+) \| "
        r"L:(?P<left>[^|]+) \| R:(?P<right>[^|]+)"
    )

    meters = {}

    try:
        lines = log_path.read_text(errors="ignore").splitlines()[-max_lines:]
    except Exception:
        return {}

    for line in lines:
        match = meter_re.search(line)
        legacy_match = None

        if not match:
            legacy_match = legacy_meter_re.search(line)
            if not legacy_match:
                continue

        if match:
            name = match.group("name").strip()
            live = match.group("live").strip()
            peak = match.group("peak").strip()
            left = match.group("left").strip()
            right = match.group("right").strip()
        else:
            name = legacy_match.group("name").strip()
            live = legacy_match.group("live").strip()
            peak = live
            left = legacy_match.group("left").strip()
            right = legacy_match.group("right").strip()

        if not name or name.startswith("===="):
            continue

        # Normalize the meter name to match how track names are cleaned
        # elsewhere in the script (clean_track_name). Storing normalized
        # names avoids mismatches where the log uses internal/user prefixes
        # or formatting that differs from the ALS track names.
        clean_name = clean_track_name(name)
        if not clean_name:
            continue

        # Console LIVE column intentionally shows PEAK-hold.
        meters[clean_name] = {
            "live": peak,
            "peak": peak,
            "left": left,
            "right": right,
        }

    return meters

def live_name_candidates(name):
    name = clean_track_name(name or "")
    candidates = []

    def add(value):
        value = clean_track_name(value or "")
        if value and value not in candidates:
            candidates.append(value)

    add(name)

    # Ableton log may use EffectiveName with numeric prefix:
    # "12 New Old Sub"
    # while the mixer row displays:
    # "New Old Sub"
    parts = name.split(" ", 1)
    if len(parts) == 2 and parts[0].isdigit():
        add(parts[1])

    for prefix in range(1, 65):
        add(f"{prefix} {name}")

    return candidates


def live_value_for(*names):
    for name in names:
        for candidate in live_name_candidates(name):
            data = LIVE_METERS.get(candidate)
            if data:
                return data.get("live", "none") or "none"
    return "none"


def read_row(track):
    name = track_name(track)
    user_name, effective_name = track_name_parts(track)
    kind = track_type(track)
    route = routing_label(track)
    route_in = input_routing_label(track)

    on = track.find("./DeviceChain/Mixer/On/Manual")
    status = "ON"
    if on is not None and val(on).lower() == "false":
        status = "MUTED"

    vol = track.find("./DeviceChain/Mixer/Volume/Manual")
    fader_db = lin_to_db(val(vol, "1"))

    pan = track.find("./DeviceChain/Mixer/Pan/Manual")
    pan_txt = pan_label(val(pan, "0"))
    sends_txt = send_values(track)

    audio_path = first_audio_clip_path(track) if kind == "AUDIO" else ""
    clip_peak = audio_peak_dbfs(audio_path)
    out_peak = clip_peak + fader_db if clip_peak is not None else None

    return {
        "name": name,
        "source_name": name,
        "user_name": user_name,
        "effective_name": effective_name,
        "type": kind,
        "route": route,
        "input": route_in,
        "status": status,
        "fader": fmt_db(fader_db),
        "fader_value": fader_db,
        "clip": fmt_peak(clip_peak),
        "clip_value": clip_peak,
        "out": fmt_peak(out_peak),
        "out_value": out_peak,
        "live": live_value_for(name, effective_name, user_name),
        "pan": pan_txt,
        "sends": sends_txt,
        "devices": device_chain(track),
        "param_details": device_param_details(track),
    }


def is_interesting(row):
    return (
        row["type"] in ["BUS", "RETURN"]
        or row["clip_value"] is not None
        or row["devices"] != "-"
        or abs(row["fader_value"]) > 0.5
        or row["pan"] != "C"
        or row.get("sends", "-") != "-"
    )


def should_keep_mixer_row(row):
    # Keep every real mixer row collected from LiveSet/Tracks.
    # A channel can be intentionally empty/default but still important for bus structure.
    # Hiding it here makes duplicate or paired tracks look like they vanished.
    if row["type"] in ["AUDIO", "MIDI", "BUS", "RETURN"]:
        return True

    return is_interesting(row)


def update_bus_values(parent, rows, children):
    child_outs = []

    for child in children.get(parent, []):
        if child["type"] == "BUS":
            child_value = update_bus_values(child["name"], rows, children)
        else:
            child_value = child.get("out_value")

        if child_value is not None:
            child_outs.append(child_value)

    parent_row = None
    for row in rows:
        if row["name"] == parent and row["type"] == "BUS":
            parent_row = row
            break

    if parent_row is None:
        return max(child_outs) if child_outs else None

    if not child_outs:
        return parent_row.get("out_value")

    # This is NOT Ableton's live meter. It is only an offline approximation:
    # the loudest child post-fader peak feeding this bus.
    bus_clip = max(child_outs)
    bus_out = bus_clip + parent_row.get("fader_value", 0.0)

    parent_row["clip_value"] = bus_clip
    parent_row["out_value"] = bus_out
    parent_row["clip"] = fmt_peak(bus_clip)
    parent_row["out"] = fmt_peak(bus_out)

    return bus_out


def line_for(row, depth):
    tree = "  " * depth + ("└─" if row["type"] in ["BUS", "RETURN"] else "├─")
    name_col = fit_col(f"{tree} {row['name']}", 32)

    if row["type"] == "BUS":
        type_label = "BUS"
    elif row["type"] == "RETURN":
        type_label = "RET"
    elif row["type"] == "MIDI":
        type_label = "MIDI"
    elif row.get("input", "-") not in ["-", "", "None"] and "Ext. In" not in row.get("input", "-"):
        type_label = "IN"
    else:
        type_label = "AUDIO"

    type_col = fit_col(type_label, 5)
    out_col = fit_col(row["out"], 11)
    live_col = fit_col(row.get("live", "none"), 10)

    clip_col = fit_col(row["clip"], 11)
    fader_col = fit_col(row["fader"], 9)
    pan_col = fit_col(row["pan"] if row["type"] not in ["BUS", "RETURN"] else "-", 5)
    send_col = fit_col(row.get("sends", "-"), 18)
    meter_col = meter_bar(out_col, width=12)
    fx_col = short_devices(row["devices"])

    return (
        f"{name_col:<32} "
        f"{type_col:<5} "
        f"{out_col:<11} "
        f"{live_col:<10} "
        f"{meter_col:<12} "
        f"{clip_col:<11} "
        f"{fader_col:<9} "
        f"{pan_col:<5} "
        f"{send_col:<18} "
        f"{fx_col}"
    )


def print_tree(parent, children, depth=0, visited=None):
    if visited is None:
        visited = set()

    for index, child in enumerate(children.get(parent, [])):
        visit_key = f"{parent}:{child['name']}:{index}:{child['type']}"
        if visit_key in visited:
            continue
        visited.add(visit_key)

        print(line_for(child, depth))

        if child["type"] == "BUS":
            print_tree(child["name"], children, depth + 1, visited)


# Print details of all rack signal paths after the main console
def print_rack_details(rows):
    rack_rows = []

    for row in rows:
        rack_paths = rack_paths_from_devices(row.get("devices", ""))
        if rack_paths:
            rack_rows.append((row["name"], rack_paths))

    if not rack_rows:
        return

    print("\n==== RACK DETAILS ====")
    for track_name_value, rack_paths in rack_rows:
        parent_name = next((row.get("parent", "-") for row in rows if row.get("name") == track_name_value), "-")
        duplicate_note = next((row.get("duplicate_of", "") for row in rows if row.get("name") == track_name_value), "")
        duplicate_text = f" duplicate_of: {duplicate_note}" if duplicate_note else ""
        alias_note = next((row.get("effective_name", "") for row in rows if row.get("name") == track_name_value and row.get("effective_name") and row.get("effective_name") != row.get("name")), "")
        alias_text = f" effective: {alias_note}" if alias_note else ""
        print(f"{track_name_value}  [parent: {parent_name}{duplicate_text}{alias_text}]")
        for index, path in enumerate(rack_paths, start=1):
            print(f"  {index}. {path}")


def print_track_index(rows):
    if not rows:
        return

    print("\n==== TRACK INDEX ====")
    print("Legend: FADER=Ableton mixer fader from saved ALS | LIVE=MixConsoleLive2 PEAK hold, stable until a higher hit arrives; NOT fader | OUT/CLIP=offline estimate | PAN=saved ALS pan")
    print(f"{'#':<4} {'TRACK':<34} {'TYPE':<6} {'PARENT':<18} {'PARAMS':<8} {'DEVICES'}")

    for index, row in enumerate(rows, start=1):
        duplicate_note = f" dup:{row.get('duplicate_of')}" if row.get("duplicate_of") else ""
        alias_note = ""
        if row.get("effective_name") and row.get("effective_name") != row.get("name"):
            alias_note = f" eff:{row.get('effective_name')}"

        track_label = f"{row.get('name', '-')}{duplicate_note}{alias_note}"
        param_count = sum(len(params) for _, params in row.get("param_details", []))
        param_text = str(param_count) if param_count else "-"
        devices = short_devices(row.get("devices", "-"))

        print(f"{index:<4} {track_label:<34} {row.get('type', '-'):<6} {row.get('parent', '-'):<18} {param_text:<8} {devices}")



def split_param_item(param):
    if isinstance(param, dict):
        return (
            param.get("path", "").strip(),
            param.get("label", "").strip(),
            param.get("value_type", "raw").strip(),
            param.get("raw_value", param.get("value", "")).strip(),
            param.get("display", param.get("raw_value", param.get("value", ""))).strip(),
        )

    if "=" in param:
        name, value = param.split("=", 1)
    elif ":" in param:
        name, value = param.split(":", 1)
    else:
        return "", param.strip(), "raw", "", ""

    return "", name.strip(), "raw", value.strip(), value.strip()





# Helper for splitting rack device path
def split_rack_device_path(device_name):
    prefix = "RACK > "
    if not device_name.startswith(prefix):
        return None, device_name

    rest = device_name[len(prefix):]
    if " > " not in rest:
        return rest.strip(), ""

    chain_name, actual_device = rest.rsplit(" > ", 1)
    return chain_name.strip(), actual_device.strip()




def device_names_from_chain(devices):
    if not devices or devices == "-":
        return []

    # Rack paths are handled from param_details/RACK CHAIN blocks.
    # For serial channel/bus device chains, split into visible device names.
    if "RACK{" in devices:
        serial_part = re.sub(r"\s*>?\s*RACK\{[^{}]*\}\s*>?\s*", " > ", devices).strip(" >")
    else:
        serial_part = devices

    if not serial_part or serial_part == "-":
        return []

    names = []
    for item in serial_part.split(" > "):
        name = item.strip()
        if name and name not in names:
            names.append(name)

    return names


def params_for_device_name(details, device_name):
    for detail_name, params in details:
        chain_name, actual_device = split_rack_device_path(detail_name)
        compare_name = actual_device if chain_name else detail_name
        if compare_name == device_name:
            return params

    return []


def box_width_for_lines(lines, minimum=96, maximum=180):
    if not lines:
        return minimum
    content_width = max(len(line) for line in lines) + 4
    return max(minimum, min(maximum, content_width))


def print_box_line(text, width, prefix="│ "):
    usable = width - 4
    if len(text) > usable:
        text = text[: usable - 1] + "…"
    print(f"{prefix}{text:<{usable}} │")


def print_box_rule(width, left="├", fill="─", right="┤"):
    print(left + (fill * (width - 2)) + right)


def print_device_params(rows):
    if not rows:
        return

    print("\n==== CHANNEL DEVICE BOXES v1 ====")
    print("Legend: every mixer channel/bus is boxed | DEVICE CHAIN = main serial chain summary | RACK CHAIN = rack-internal path | PARAMS = changed/non-default values only")

    for row in rows:
        track_name_value = row.get("name", "-")
        details = row.get("param_details", [])
        parent_name = row.get("parent", "-")
        duplicate_note = row.get("duplicate_of", "")
        duplicate_text = f" duplicate_of: {duplicate_note}" if duplicate_note else ""
        alias_note = row.get("effective_name", "") if row.get("effective_name") and row.get("effective_name") != row.get("name") else ""
        alias_text = f" effective: {alias_note}" if alias_note else ""

        raw_devices = row.get("devices", "-")
        devices_text = short_devices(raw_devices)
        device_chain_text = devices_text if devices_text != "-" else "none"

        lines = []
        title = f"{track_name_value}  [parent: {parent_name}{duplicate_text}{alias_text}]"
        lines.append(title)
        lines.append(f"DEVICE CHAIN: {device_chain_text}")

        blocks = []

        for device_name in device_names_from_chain(raw_devices):
            blocks.append({
                "kind": "device",
                "name": device_name,
                "params": params_for_device_name(details, device_name),
            })

        current_chain = None
        for detail_name, params in details:
            chain_name, actual_device = split_rack_device_path(detail_name)
            if not chain_name:
                continue

            if chain_name != current_chain:
                blocks.append({"kind": "chain", "name": chain_name, "params": []})
                current_chain = chain_name

            blocks.append({"kind": "device", "name": actual_device, "params": params})

        if not blocks:
            lines.append("PARAMS: none detected")
        else:
            for block in blocks:
                if block["kind"] == "chain":
                    lines.append(f"RACK CHAIN: {block['name']}")
                    continue

                lines.append(f"DEVICE: {block['name']}")
                params = block.get("params", [])

                if not params:
                    lines.append("  PARAMS: none detected")
                    continue

                for param in params:
                    param_path, param_label_value, param_type, param_raw, param_display = split_param_item(param)
                    if param_path:
                        lines.append(f"  {param_path:<48} {param_label_value:<24} {param_type:<16} raw={param_raw:<10} display={param_display}")
                    elif param_raw:
                        lines.append(f"  {'-':<48} {param_label_value:<24} {param_type:<16} raw={param_raw:<10} display={param_display}")
                    else:
                        lines.append(f"  {param_label_value}")

        width = box_width_for_lines(lines)
        print_box_rule(width, "┌", "─", "┐")
        print_box_line(lines[0], width)
        print_box_rule(width, "├", "─", "┤")
        for line in lines[1:]:
            print_box_line(line, width)
        print_box_rule(width, "└", "─", "┘")


def collect_mixer_tracks(root):
    """Return real mixer tracks in Ableton mixer order.

    Do not use root.iter() for track collection. ALS may contain track-like
    nodes in nested/frozen/internal structures. Only direct children of the
    main Tracks and ReturnTracks containers should become mixer rows.
    """
    mixer_tracks = []
    live_set = root.find("LiveSet")
    if live_set is None:
        live_set = root.find("./LiveSet")
    if live_set is None:
        live_set = root

    tracks_container = live_set.find("Tracks")
    if tracks_container is None:
        tracks_container = root.find(".//LiveSet/Tracks")
    if tracks_container is None:
        tracks_container = root.find(".//Tracks")
    if tracks_container is not None:
        for child in list(tracks_container):
            if child.tag in ["AudioTrack", "MidiTrack", "GroupTrack"]:
                mixer_tracks.append(child)

    return_tracks_container = live_set.find("ReturnTracks")
    if return_tracks_container is None:
        return_tracks_container = root.find(".//LiveSet/ReturnTracks")
    if return_tracks_container is None:
        return_tracks_container = root.find(".//ReturnTracks")
    if return_tracks_container is not None:
        for child in list(return_tracks_container):
            if child.tag == "ReturnTrack":
                mixer_tracks.append(child)

    return mixer_tracks


LIVE_METERS = load_live_meters()

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

rows = []
name_counts = {}

for track in collect_mixer_tracks(root):
    row = read_row(track)
    if not should_keep_mixer_row(row):
        continue

    base_name = row["name"]
    duplicate_key = (base_name, row["type"])
    name_counts[duplicate_key] = name_counts.get(duplicate_key, 0) + 1

    if name_counts[duplicate_key] > 1:
        row["name"] = f"{base_name} [{name_counts[duplicate_key]}]"
        row["duplicate_of"] = base_name
    else:
        row["duplicate_of"] = ""

    rows.append(row)

for index, row in enumerate(rows):
    row["parent"] = parent_for(rows, index)

children = {}
for row in rows:
    children.setdefault(row["parent"], []).append(row)

update_bus_values("MASTER", rows, children)

if REPORT_PAGE not in ["all", "console", "devices"]:
    raise SystemExit("Unknown page. Use: --page all|console|devices")

# Terminal scrolls upward, so print detail pages first and the main mixer last.
# In --page all, the final visible section should be MIX CONSOLE.
if REPORT_PAGE in ["all", "devices"]:
    print_device_params(rows)

if REPORT_PAGE in ["all", "console"]:
    print("==== MIX CONSOLE v3.67-LIVE-USES-PEAK ====")
    print("Legend: FADER=Ableton mixer fader from saved ALS | PEAK=MixConsoleLive2 PEAK hold value | OUT/CLIP=offline estimate | PAN=saved ALS pan")
    print(f"{'CHANNEL':<32} {'TYPE':<4} {'OUT':<11} {'PEAK':<10} {'METER':<12} {'CLIP':<11} {'FADER':<9} {'PAN':<5} {'SEND':<18} FX")
    print("-" * 140)
    print(f"{'MASTER':<32} {'MAIN':<4} {'-':<11} {live_value_for('Main'):<10} {'░' * 12:<12} {'-':<11} {'-':<9} {'-':<5} {'-':<18} -")
    # Print only top-level mixer roots.
    # A top-level root is a parent name that is not itself a child row name.
    row_names = {r["name"] for r in rows}
    printed = set()

    for parent_name in ["MASTER", "Main", "Master", "-", "", "None"]:
        if parent_name in children:
            print_tree(parent_name, children)
            printed.add(parent_name)

    top_roots = sorted(
        parent_name for parent_name in children.keys()
        if parent_name not in printed and parent_name not in row_names
    )

    for parent_name in top_roots:
        print_tree(parent_name, children)
