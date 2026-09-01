"""Ableton .als icine otomasyon zarfi yazar.

Until now this was the one thing the project could only READ (GAP-002/005):
als_automation_inspector could list envelopes but nothing could create one.

How it works: every mixer parameter already carries an `AutomationTarget Id`
in the XML, and an envelope points at it through `EnvelopeTarget/PointeeId`.
The target is never invented -- the id Live itself wrote is the one used.

Fail-closed:
  * Only parameters that resolve are written
  * Values may not leave the parameter's own MidiControllerRange
  * An existing envelope on the same target needs replace=True
  * The file is read back and compared after writing
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET

# Live's "beginning of time" marker: the value before the first point sits here.
BEGINNING_OF_TIME = "-63072000"

# The starting scope is mixer parameters: their shape is the same in every
# project and their ranges are declared in the XML. Device parameters reach
# through the same mechanism via find_target_by_pointee.
PARAMETER_PATHS = {
    "volume": "./DeviceChain/Mixer/Volume",
    "pan": "./DeviceChain/Mixer/Pan",
}


class AutomationWriteError(ValueError):
    pass


@dataclass(frozen=True)
class AutomationTargetInfo:
    parameter: str
    pointee_id: str
    minimum: float
    maximum: float
    manual: float


@dataclass(frozen=True)
class AutomationWriteResult:
    track_name: str
    parameter: str
    pointee_id: str
    point_count: int
    replaced: bool


def db_to_linear(value_db: float) -> float:
    return 10 ** (value_db / 20.0)


def linear_to_db(value: float) -> float | None:
    return 20 * math.log10(value) if value > 0 else None


def _float(node: ET.Element | None, default: float | None = None) -> float | None:
    if node is None or "Value" not in node.attrib:
        return default
    try:
        return float(node.attrib["Value"])
    except ValueError:
        return default


def find_automation_target(track: ET.Element, parameter: str) -> AutomationTargetInfo:
    path = PARAMETER_PATHS.get(parameter)
    if path is None:
        raise AutomationWriteError(
            f"unsupported_parameter: {parameter!r}. Supported: {', '.join(sorted(PARAMETER_PATHS))}"
        )
    node = track.find(path)
    if node is None:
        raise AutomationWriteError(f"parameter_not_found_on_track: {parameter}")
    target = node.find("./AutomationTarget")
    if target is None or "Id" not in target.attrib:
        raise AutomationWriteError(f"no_automation_target: {parameter} has no AutomationTarget id")
    return AutomationTargetInfo(
        parameter=parameter,
        pointee_id=target.attrib["Id"],
        minimum=_float(node.find("./MidiControllerRange/Min"), 0.0),
        maximum=_float(node.find("./MidiControllerRange/Max"), 1.0),
        manual=_float(node.find("./Manual"), 0.0),
    )


def list_automatable_parameters(track: ET.Element) -> list[dict]:
    """Bu track'te otomasyonu yazilabilecek her parametre.

    The rule is simple and exact: an element whose automation can be written
    has both a `Manual` value and its own `AutomationTarget Id`. Device
    parameters are therefore found without inventing anything -- the id Live
    itself wrote is used. The trade-off is that parameter names are XML tags,
    not the names Live shows on screen.
    """
    found = []
    for scope, root_node in (("mixer", track.find("./DeviceChain/Mixer")), ("device", track.find("./DeviceChain/DeviceChain/Devices"))):
        if root_node is None:
            continue
        for element in root_node.iter():
            target = element.find("./AutomationTarget")
            manual = element.find("./Manual")
            if target is None or manual is None or "Id" not in target.attrib:
                continue
            found.append({
                "scope": scope,
                "tag": element.tag,
                "pointee_id": target.attrib["Id"],
                "current_value": _float(manual),
                "min": _float(element.find("./MidiControllerRange/Min")),
                "max": _float(element.find("./MidiControllerRange/Max")),
            })
    return found


def find_target_by_pointee(track: ET.Element, pointee_id: str) -> AutomationTargetInfo:
    """Kesfedilmis bir PointeeId ile hedefi cozer (cihaz parametreleri icin)."""
    for entry in list_automatable_parameters(track):
        if entry["pointee_id"] == pointee_id:
            if entry["min"] is None or entry["max"] is None:
                raise AutomationWriteError(
                    f"no_declared_range: {entry['tag']} has no MidiControllerRange, refusing to guess bounds"
                )
            return AutomationTargetInfo(
                parameter=entry["tag"],
                pointee_id=pointee_id,
                minimum=entry["min"],
                maximum=entry["max"],
                manual=entry["current_value"] or 0.0,
            )
    raise AutomationWriteError(f"unknown_pointee_id: {pointee_id} is not an automatable parameter on this track")


def _envelopes_container(track: ET.Element) -> ET.Element:
    holder = track.find("./AutomationEnvelopes")
    if holder is None:
        raise AutomationWriteError("track_has_no_automation_envelopes_container")
    envelopes = holder.find("./Envelopes")
    if envelopes is None:
        envelopes = ET.SubElement(holder, "Envelopes")
    return envelopes


def _existing_envelope(envelopes: ET.Element, pointee_id: str) -> ET.Element | None:
    for envelope in envelopes.findall("./AutomationEnvelope"):
        node = envelope.find("./EnvelopeTarget/PointeeId")
        if node is not None and node.attrib.get("Value") == pointee_id:
            return envelope
    return None


def normalise_points(points, target: AutomationTargetInfo, unit: str = "native") -> list[tuple[float, float]]:
    if not points:
        raise AutomationWriteError("no_points: an envelope needs at least one point")
    normalised = []
    previous_time = None
    for index, point in enumerate(points):
        try:
            time_beats = float(point["time"])
            raw_value = float(point["value"])
        except (KeyError, TypeError, ValueError) as error:
            raise AutomationWriteError(f"point {index}: needs numeric 'time' and 'value'") from error
        if time_beats < 0:
            raise AutomationWriteError(f"point {index}: time must be >= 0")
        if previous_time is not None and time_beats < previous_time:
            raise AutomationWriteError(f"point {index}: times must not go backwards")
        previous_time = time_beats

        value = db_to_linear(raw_value) if unit == "db" else raw_value
        if unit == "db" and target.parameter != "volume":
            raise AutomationWriteError("db_unit_only_valid_for_volume")
        if not (target.minimum <= value <= target.maximum):
            raise AutomationWriteError(
                f"point {index}: value {value:g} outside the parameter range "
                f"[{target.minimum:g}, {target.maximum:g}]"
            )
        normalised.append((time_beats, value))
    return normalised


def resolve_target(track: ET.Element, parameter: str = "", pointee_id: str = "") -> AutomationTargetInfo:
    if pointee_id:
        return find_target_by_pointee(track, pointee_id)
    return find_automation_target(track, parameter)


def write_automation(
    track: ET.Element,
    parameter: str = "",
    points=None,
    *,
    unit: str = "native",
    replace: bool = False,
    track_name: str = "",
    pointee_id: str = "",
) -> AutomationWriteResult:
    target = resolve_target(track, parameter, pointee_id)
    normalised = normalise_points(points, target, unit)

    envelopes = _envelopes_container(track)
    existing = _existing_envelope(envelopes, target.pointee_id)
    if existing is not None and not replace:
        raise AutomationWriteError(
            f"envelope_exists: {parameter} on {track_name or 'this track'} already has automation. "
            "Pass replace=true to overwrite it."
        )
    if existing is not None:
        envelopes.remove(existing)

    used_ids = {
        int(node.attrib["Id"])
        for node in envelopes.findall("./AutomationEnvelope")
        if node.attrib.get("Id", "").lstrip("-").isdigit()
    }
    envelope_id = 0
    while envelope_id in used_ids:
        envelope_id += 1

    envelope = ET.SubElement(envelopes, "AutomationEnvelope", {"Id": str(envelope_id)})
    envelope_target = ET.SubElement(envelope, "EnvelopeTarget")
    ET.SubElement(envelope_target, "PointeeId", {"Value": target.pointee_id})
    automation = ET.SubElement(envelope, "Automation")
    events = ET.SubElement(automation, "Events")

    # The value before the first point: the parameter's current manual value.
    ET.SubElement(events, "FloatEvent", {"Id": "0", "Time": BEGINNING_OF_TIME, "Value": repr(target.manual)})
    for index, (time_beats, value) in enumerate(normalised, start=1):
        ET.SubElement(events, "FloatEvent", {"Id": str(index), "Time": repr(time_beats), "Value": repr(value)})

    view_state = ET.SubElement(automation, "AutomationTransformViewState")
    ET.SubElement(view_state, "IsTransformPending", {"Value": "false"})
    ET.SubElement(view_state, "TimeAndValueTransforms")

    return AutomationWriteResult(
        track_name=track_name,
        parameter=target.parameter,
        pointee_id=target.pointee_id,
        point_count=len(normalised),
        replaced=existing is not None,
    )


def read_automation(track: ET.Element, parameter: str = "", pointee_id: str = "") -> list[tuple[float, float]]:
    """Read the written envelope back. The beginning-of-time marker is skipped."""
    target = resolve_target(track, parameter, pointee_id)
    envelopes = track.find("./AutomationEnvelopes/Envelopes")
    if envelopes is None:
        return []
    envelope = _existing_envelope(envelopes, target.pointee_id)
    if envelope is None:
        return []
    points = []
    for event in envelope.findall("./Automation/Events/FloatEvent"):
        time_raw = event.attrib.get("Time", "")
        if time_raw == BEGINNING_OF_TIME:
            continue
        points.append((float(time_raw), float(event.attrib.get("Value", "0"))))
    return points


def verify_automation(track: ET.Element, parameter: str = "", expected=None, pointee_id: str = "") -> None:
    expected = expected or []
    written = read_automation(track, parameter, pointee_id)
    if len(written) != len(expected):
        raise AutomationWriteError(f"verification_failed: expected {len(expected)} points, found {len(written)}")
    for index, ((expected_time, expected_value), (actual_time, actual_value)) in enumerate(zip(expected, written)):
        if abs(expected_time - actual_time) > 1e-6 or abs(expected_value - actual_value) > 1e-9:
            raise AutomationWriteError(
                f"verification_failed at point {index}: wrote ({expected_time}, {expected_value}), "
                f"read back ({actual_time}, {actual_value})"
            )
