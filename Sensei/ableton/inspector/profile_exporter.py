from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Optional

from ableton.inspector.alc_inspector import read_ableton_xml, extract_drum_pads_loose
from ableton.inspector.midi_reader import read_midi_events


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "kit"


def infer_mpe_like(pads: list[dict]) -> bool:
    if not pads:
        return False
    notes = [int(p["note"]) for p in pads]
    return min(notes) >= 70 and max(notes) <= 100 and len(notes) >= 8


def find_param_raw_value(parent, param_name: str) -> tuple[Optional[str], Optional[str]]:
    paths = [
        f"{param_name}/Manual",
        f".//{param_name}/Manual",
        f"VolumeAndPan/{param_name}/Manual",
        f".//VolumeAndPan/{param_name}/Manual",
        f"Filter/{param_name}/Manual",
        f".//Filter/{param_name}/Manual",
        f"VolumeAndPan/Envelope/{param_name}/Manual",
        f".//VolumeAndPan/Envelope/{param_name}/Manual",
    ]
    for p in paths:
        found = parent.find(p)
        if found is not None:
            val = found.attrib.get("Value")
            if val is not None:
                return val, p
    return None, None


def extract_device_param_details(device_elem) -> dict:
    if device_elem is None:
        return {
            "device_type": "unknown",
            "effect_types": (),
            "decay": None,
            "release": None,
            "sustain": None,
            "filter_enabled": None,
            "filter_type": None,
            "filter_cutoff": None,
            "choke_group": None,
            "parse_source": "none",
            "warnings": (),
            "decay_raw": None,
            "release_raw": None,
            "time_unit": "unknown",
            "source_path": "none"
        }

    device_type = device_elem.tag
    warnings_list = []
    parse_sources = {}

    decay = None
    release = None
    sustain = None

    decay_raw, decay_path = find_param_raw_value(device_elem, "DecayTime")
    if decay_raw is not None:
        try:
            decay = float(decay_raw)
            parse_sources["decay"] = decay_path
        except ValueError:
            warnings_list.append(f"Failed to parse DecayTime value: {decay_raw}")

    release_raw, release_path = find_param_raw_value(device_elem, "ReleaseTime")
    if release_raw is not None:
        try:
            release = float(release_raw)
            parse_sources["release"] = release_path
        except ValueError:
            warnings_list.append(f"Failed to parse ReleaseTime value: {release_raw}")

    sustain_raw, sustain_path = find_param_raw_value(device_elem, "SustainLevel")
    if sustain_raw is not None:
        try:
            sustain = float(sustain_raw)
            parse_sources["sustain"] = sustain_path
        except ValueError:
            warnings_list.append(f"Failed to parse SustainLevel value: {sustain_raw}")

    filter_enabled = None
    filter_type = None
    filter_cutoff = None

    filter_on_raw, filter_on_path = find_param_raw_value(device_elem, "IsOn")
    if filter_on_raw is not None:
        filter_enabled = filter_on_raw.lower() == "true"
        parse_sources["filter_enabled"] = filter_on_path

    filter_type_raw, filter_type_path = find_param_raw_value(device_elem, "Type")
    if filter_type_raw is not None:
        try:
            type_idx = int(float(filter_type_raw))
            filter_type_map = {
                0: "LowPass",
                1: "HighPass",
                2: "BandPass",
                3: "Notch",
                4: "Morph"
            }
            filter_type = filter_type_map.get(type_idx, "unknown")
            parse_sources["filter_type"] = filter_type_path
        except ValueError:
            warnings_list.append(f"Failed to parse filter Type value: {filter_type_raw}")
            filter_type = "unknown"

    filter_cutoff_raw, filter_cutoff_path = find_param_raw_value(device_elem, "Freq")
    if filter_cutoff_raw is not None:
        try:
            filter_cutoff = float(filter_cutoff_raw)
            parse_sources["filter_cutoff"] = filter_cutoff_path
        except ValueError:
            warnings_list.append(f"Failed to parse filter cutoff Freq: {filter_cutoff_raw}")

    # Warn if Loop mode or One-Shot modes are active
    loop_mode_raw, loop_mode_path = find_param_raw_value(device_elem, "LoopMode")
    if loop_mode_raw is not None and loop_mode_raw != "0":
        warnings_list.append("Actual tail length is not measured (Loop mode active)")

    oneshot_env = device_elem.find(".//VolumeAndPan/OneShotEnvelope")
    if oneshot_env is not None:
        warnings_list.append("Actual tail length is not measured (One-Shot/Trigger/Gate active)")

    if parse_sources:
        parse_source = "; ".join(f"{k}:{v}" for k, v in sorted(parse_sources.items()))
    else:
        parse_source = "none"

    time_unit = "ms_verified" if device_type in {"OriginalSimpler", "MultiSampler"} else "ms_assumed"

    return {
        "device_type": device_type,
        "decay": decay,
        "release": release,
        "sustain": sustain,
        "filter_enabled": filter_enabled,
        "filter_type": filter_type,
        "filter_cutoff": filter_cutoff,
        "parse_source": parse_source,
        "warnings": tuple(warnings_list),
        "decay_raw": decay,
        "release_raw": release,
        "time_unit": time_unit,
        "source_path": parse_source
    }


def extract_branch_samples(branch) -> tuple[list[dict], Optional[dict]]:
    sample_files = []

    # 1. Search inside MultiSamplePart (Sampler)
    for part in branch.iter("MultiSamplePart"):
        fr_el = part.find(".//SampleRef/FileRef")
        if fr_el is None:
            fr_el = part.find(".//FileRef")
        if fr_el is not None:
            path_el = fr_el.find("Path")
            rel_path_el = fr_el.find("RelativePath")
            path_val = path_el.attrib.get("Value") if path_el is not None else None
            rel_path_val = rel_path_el.attrib.get("Value") if rel_path_el is not None else None
            best_path = path_val or rel_path_val
            if best_path:
                p = Path(best_path)
                suffix = p.suffix.lower()
                if suffix in {".wav", ".aif", ".aiff", ".mp3", ".ogg", ".flac", ".mp4", ".m4a"}:
                    k_min, k_max = None, None
                    v_min, v_max = None, None
                    
                    kr = part.find("KeyRange")
                    if kr is not None:
                        min_el = kr.find("Min")
                        max_el = kr.find("Max")
                        if min_el is not None and max_el is not None:
                            try:
                                k_min = int(min_el.attrib.get("Value", "0"))
                                k_max = int(max_el.attrib.get("Value", "127"))
                            except ValueError:
                                pass
                                
                    vr = part.find("VelocityRange")
                    if vr is not None:
                        min_el = vr.find("Min")
                        max_el = vr.find("Max")
                        if min_el is not None and max_el is not None:
                            try:
                                v_min = int(min_el.attrib.get("Value", "1"))
                                v_max = int(max_el.attrib.get("Value", "127"))
                            except ValueError:
                                pass
                                
                    sample_files.append({
                        "filename": p.name,
                        "path": str(p),
                        "key_range": (k_min, k_max) if (k_min is not None and k_max is not None) else None,
                        "velocity_range": (v_min, v_max) if (v_min is not None and v_max is not None) else None
                    })

    # 2. Search inside SampleInfo (Simpler / fallbacks)
    if not sample_files:
        for sample_info in branch.iter("SampleInfo"):
            fr_el = sample_info.find(".//FileRef")
            if fr_el is not None:
                path_el = fr_el.find("Path")
                rel_path_el = fr_el.find("RelativePath")
                path_val = path_el.attrib.get("Value") if path_el is not None else None
                rel_path_val = rel_path_el.attrib.get("Value") if rel_path_el is not None else None
                best_path = path_val or rel_path_val
                if best_path:
                    p = Path(best_path)
                    suffix = p.suffix.lower()
                    if suffix in {".wav", ".aif", ".aiff", ".mp3", ".ogg", ".flac", ".mp4", ".m4a"}:
                        key_min_el = sample_info.find(".//KeyRangeMin")
                        key_max_el = sample_info.find(".//KeyRangeMax")
                        vel_min_el = sample_info.find(".//VelocityRangeMin")
                        vel_max_el = sample_info.find(".//VelocityRangeMax")

                        k_min = int(key_min_el.attrib.get("Value")) if key_min_el is not None else None
                        k_max = int(key_max_el.attrib.get("Value")) if key_max_el is not None else None
                        v_min = int(vel_min_el.attrib.get("Value")) if vel_min_el is not None else None
                        v_max = int(vel_max_el.attrib.get("Value")) if vel_max_el is not None else None

                        sample_files.append({
                            "filename": p.name,
                            "path": str(p),
                            "key_range": (k_min, k_max) if (k_min is not None and k_max is not None) else None,
                            "velocity_range": (v_min, v_max) if (v_min is not None and v_max is not None) else None
                        })

    # 3. Fallback to global FileRefs
    if not sample_files:
        for fr in branch.iter("FileRef"):
            path_el = fr.find("Path")
            rel_path_el = fr.find("RelativePath")
            path_val = path_el.attrib.get("Value") if path_el is not None else None
            rel_path_val = rel_path_el.attrib.get("Value") if rel_path_el is not None else None
            best_path = path_val or rel_path_val
            if best_path:
                p = Path(best_path)
                if p.suffix.lower() in {".wav", ".aif", ".aiff", ".mp3", ".ogg", ".flac", ".mp4", ".m4a"}:
                    if not any(f["path"] == str(p) for f in sample_files):
                        sample_files.append({
                            "filename": p.name,
                            "path": str(p),
                            "key_range": None,
                            "velocity_range": None
                        })

    primary_sample = None
    if sample_files:
        def get_coverage(sf):
            k = sf.get("key_range")
            v = sf.get("velocity_range")
            k_w = (k[1] - k[0] + 1) if k else 1
            v_w = (v[1] - v[0] + 1) if v else 1
            return k_w * v_w
        sorted_samples = sorted(sample_files, key=lambda x: get_coverage(x), reverse=True)
        primary_sample = sorted_samples[0]

    return sample_files, primary_sample


def extract_sonic_hints(filename: str, path: str) -> dict:
    filename_lower = filename.lower() if filename else ""
    path_lower = path.lower() if path else ""

    low_end_hint = None
    if any(k in filename_lower or k in path_lower for k in ["808", "sub", "heavy", "fat", "boom"]):
        low_end_hint = {
            "value": "heavy",
            "evidence": f"Found low-end keyword in filename: {filename}",
            "confidence": 0.85
        }
    elif any(k in filename_lower or k in path_lower for k in ["acoustic", "rim", "shaker", "clap", "hat", "click", "light"]):
        low_end_hint = {
            "value": "light",
            "evidence": f"Found transient/high-end keyword in filename: {filename}",
            "confidence": 0.70
        }
    else:
        low_end_hint = {
            "value": "medium",
            "evidence": "No specific low-end keywords matched",
            "confidence": 0.50
        }

    tail_class = None
    if any(k in filename_lower or k in path_lower for k in ["long", "tail", "sustained", "decay"]):
        tail_class = {
            "value": "long",
            "evidence": f"Found tail length keyword in filename: {filename}",
            "confidence": 0.85
        }
    elif any(k in filename_lower or k in path_lower for k in ["short", "tight", "staccato", "dry", "gate"]):
        tail_class = {
            "value": "short",
            "evidence": f"Found short decay keyword in filename: {filename}",
            "confidence": 0.80
        }
    else:
        tail_class = {
            "value": "medium",
            "evidence": "No specific tail length keywords matched",
            "confidence": 0.50
        }

    source_character = None
    electronic_kws = ["808", "909", "707", "606", "linn", "synth", "electronic", "drumulator", "tr-", "tr808", "tr909", "synthesized"]
    acoustic_kws = ["acoustic", "session", "live", "studio", "drummer", "real", "natural", "organic", "wood", "metal"]

    has_elec = any(k in filename_lower or k in path_lower for k in electronic_kws)
    has_acou = any(k in filename_lower or k in path_lower for k in acoustic_kws)

    if has_elec and has_acou:
        source_character = {
            "value": "hybrid",
            "evidence": f"Found both electronic and acoustic keywords in filename: {filename}",
            "confidence": 0.75
        }
    elif has_elec:
        source_character = {
            "value": "electronic",
            "evidence": f"Found electronic keyword in filename: {filename}",
            "confidence": 0.90
        }
    elif has_acou:
        source_character = {
            "value": "acoustic",
            "evidence": f"Found acoustic keyword in filename: {filename}",
            "confidence": 0.90
        }
    else:
        source_character = {
            "value": "unknown",
            "evidence": "No specific source character keywords matched",
            "confidence": 0.30
        }

    return {
        "low_end_hint": low_end_hint,
        "tail_class": tail_class,
        "source_character": source_character
    }


def aggregate_kit_level_fields(pads_dict: dict) -> dict:
    core_pads = [p for p in pads_dict.values() if p.get("pad_semantic_group") == "drum_core"]
    if not core_pads:
        core_pads = list(pads_dict.values())

    decays = []
    releases = []
    for p in core_pads:
        dp = p.get("device_profile")
        if dp:
            if dp.get("decay") is not None:
                decays.append(dp["decay"])
            if dp.get("release") is not None:
                releases.append(dp["release"])

    mean_decay = sum(decays) / len(decays) if decays else None
    mean_release = sum(releases) / len(releases) if releases else None

    tail_class_votes = {}
    source_character_votes = {}
    low_end_hint_votes = {}

    for p in core_pads:
        sh = p.get("sonic_hints")
        if sh:
            for k, votes in [("tail_class", tail_class_votes), ("source_character", source_character_votes), ("low_end_hint", low_end_hint_votes)]:
                hint = sh.get(k)
                if hint and hint.get("value"):
                    val = hint["value"]
                    votes[val] = votes.get(val, 0.0) + hint.get("confidence", 0.5)

    def pick_winner(votes: dict, fallback: str) -> str:
        if not votes:
            return fallback
        sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
        return sorted_votes[0][0]

    tail_class = pick_winner(tail_class_votes, "unknown")
    source_character = pick_winner(source_character_votes, "unknown")
    low_end_hint = pick_winner(low_end_hint_votes, "unknown")

    if tail_class == "unknown" and mean_decay is not None:
        if mean_decay < 300:
            tail_class = "short"
        elif mean_decay > 1200:
            tail_class = "long"
        else:
            tail_class = "medium"

    max_samples = max([len(p.get("sample_files", [])) for p in core_pads]) if core_pads else 1
    if max_samples > 4:
        layer_complexity = "high"
    elif max_samples > 1:
        layer_complexity = "medium"
    else:
        layer_complexity = "low"

    # Role-specific summaries (deterministik agregasyon)
    by_role = {}
    for note, p in pads_dict.items():
        r = p.get("normalized_role")
        if r and r != "unknown_pad":
            if r not in by_role:
                by_role[r] = []
            by_role[r].append(p)

    role_summaries = {}
    for r, r_pads in sorted(by_role.items()):
        r_decays = []
        r_releases = []
        r_tail_votes = {}
        r_low_end_votes = {}
        r_confidences = []
        evidence_count = 0

        for p in r_pads:
            dp = p.get("device_profile")
            if dp:
                has_evidence = dp.get("decay") is not None or dp.get("release") is not None
                if has_evidence:
                    evidence_count += 1
                if dp.get("decay") is not None:
                    r_decays.append(dp["decay"])
                if dp.get("release") is not None:
                    r_releases.append(dp["release"])

            r_confidences.append(p.get("confidence", 0.0))

            sh = p.get("sonic_hints")
            if sh:
                tc = sh.get("tail_class")
                le = sh.get("low_end_hint")
                if tc and tc.get("value"):
                    r_tail_votes[tc["value"]] = r_tail_votes.get(tc["value"], 0.0) + tc.get("confidence", 0.5)
                if le and le.get("value"):
                    r_low_end_votes[le["value"]] = r_low_end_votes.get(le["value"], 0.0) + le.get("confidence", 0.5)

        mean_decay_ms = sum(r_decays) / len(r_decays) if r_decays else None
        mean_release_ms = sum(r_releases) / len(r_releases) if r_releases else None
        max_decay_ms = max(r_decays) if r_decays else None

        role_tail = pick_winner(r_tail_votes, "unknown")
        if role_tail == "unknown" and mean_decay_ms is not None:
            if mean_decay_ms < 300:
                role_tail = "short"
            elif mean_decay_ms > 1200:
                role_tail = "long"
            else:
                role_tail = "medium"

        role_low_end = pick_winner(r_low_end_votes, "unknown")
        mean_conf = sum(r_confidences) / len(r_confidences) if r_confidences else 0.0

        role_summaries[r] = {
            "mean_decay_ms": mean_decay_ms,
            "mean_release_ms": mean_release_ms,
            "max_decay_ms": max_decay_ms,
            "tail_class": role_tail,
            "low_end_hint": role_low_end,
            "confidence": round(mean_conf, 3),
            "pad_count": len(r_pads),
            "evidence_count": evidence_count
        }

    return {
        "mean_decay": mean_decay,
        "mean_release": mean_release,
        "tail_class": tail_class,
        "source_character": source_character,
        "low_end_hint": low_end_hint,
        "layer_complexity": layer_complexity,
        "role_summaries": role_summaries
    }


def inspect_kit_device_chains(root, path: Path) -> dict:
    has_sampler = False
    has_simpler = False
    effect_count = 0
    macro_count = 0
    active_macros = []
    complexity_ratings = []
    pad_chains = {}

    num_visible_el = root.find(".//NumVisibleMacroControls")
    num_visible_macros = 8
    if num_visible_el is not None:
        try:
            num_visible_macros = int(num_visible_el.attrib.get("Value", "8"))
        except ValueError:
            pass

    macro_names = []
    for i in range(16):
        disp_name_el = root.find(f".//MacroDisplayNames.{i}")
        if disp_name_el is not None:
            name_val = disp_name_el.attrib.get("Value", "").strip()
            macro_names.append(name_val)
        else:
            macro_names.append("")
    active_macros = [n for n in macro_names if n]
    macro_count = max(len(active_macros), num_visible_macros) if active_macros else num_visible_macros

    branches = list(root.iter("DrumBranch")) + list(root.iter("DrumBranchPreset"))
    drum_core_pads = 0
    performance_pads = 0
    unknown_pads = 0

    for branch in branches:
        label = ""
        for child in branch.iter():
            if child.tag in {"Name", "UserName"}:
                val = (child.attrib.get("Value") or child.text or "").strip()
                if val:
                    label = val

        rec_el = branch.find(".//ReceivingNote")
        rec_val = rec_el.attrib.get("Value") if rec_el is not None else None
        if rec_val is None:
            continue
        try:
            note = int(float(rec_val))
        except ValueError:
            continue

        choke_el = branch.find(".//ChokeGroup")
        choke_group = None
        if choke_el is not None:
            val = choke_el.attrib.get("Value")
            if val and val != "0":
                try:
                    choke_group = int(val)
                except ValueError:
                    pass

        devices = []
        if branch.tag == "DrumBranchPreset":
            preset_list = branch.find("DevicePresets")
            if preset_list is not None:
                for ab_preset in preset_list.findall("AbletonDevicePreset"):
                    dev = ab_preset.find("Device")
                    if dev is not None and len(dev) > 0:
                        devices.append(list(dev)[0].tag)
        else:
            devices_container = branch.find(".//DeviceChain/MidiToAudioDeviceChain/Devices")
            if devices_container is not None:
                devices = [c.tag for c in devices_container]

        branch_has_sampler = any(d == "MultiSampler" for d in devices)
        branch_has_simpler = any(d == "OriginalSimpler" for d in devices)
        if branch_has_sampler:
            has_sampler = True
        if branch_has_simpler:
            has_simpler = True

        effects = [d for d in devices if d not in {"MultiSampler", "OriginalSimpler", "InstrumentGroupDevice"}]
        effect_count += len(effects)

        if any(d == "InstrumentGroupDevice" for d in devices):
            chain_complexity = "high"
        elif len(effects) > 1:
            chain_complexity = "medium"
        else:
            chain_complexity = "low"
        complexity_ratings.append(chain_complexity)

        sample_files, primary_sample = extract_branch_samples(branch)

        simpler_elem = branch.find(".//OriginalSimpler")
        sampler_elem = branch.find(".//MultiSampler")
        device_elem = simpler_elem if simpler_elem is not None else sampler_elem

        device_profile = extract_device_param_details(device_elem)
        device_profile["choke_group"] = choke_group
        device_profile["effect_types"] = tuple(effects)

        p_filename = primary_sample["filename"] if primary_sample else ""
        p_path = primary_sample["path"] if primary_sample else ""
        sonic_hints = extract_sonic_hints(p_filename, p_path)

        group, role_val, conf = classify_pad_by_label(label)
        if group == "drum_core":
            drum_core_pads += 1
        elif group == "performance_pad":
            performance_pads += 1
        else:
            unknown_pads += 1

        pad_chains[str(note)] = {
            "label": label,
            "choke_group": choke_group,
            "devices": devices,
            "has_sampler": branch_has_sampler,
            "has_simpler": branch_has_simpler,
            "effects": effects,
            "chain_complexity": chain_complexity,
            "semantic_group": group,
            "normalized_role": role_val,
            "confidence": conf,
            "device_profile": device_profile,
            "sonic_hints": sonic_hints,
            "sample_files": sample_files,
            "primary_sample": primary_sample
        }

    overall_complexity = "low"
    if "high" in complexity_ratings:
        overall_complexity = "high"
    elif "medium" in complexity_ratings:
        overall_complexity = "medium"

    if drum_core_pads == 0:
        kit_write_safety = "unsafe"
    elif overall_complexity == "high" or unknown_pads > drum_core_pads:
        kit_write_safety = "caution"
    else:
        kit_write_safety = "safe"

    return {
        "has_sampler": has_sampler,
        "has_simpler": has_simpler,
        "effect_count": effect_count,
        "macro_count": macro_count,
        "active_macros": active_macros,
        "chain_complexity": overall_complexity,
        "kit_write_safety": kit_write_safety,
        "device_chain_summary": pad_chains,
    }


def classify_pad_by_label(label: str) -> tuple[str, str, float]:
    import re
    lbl_lower = label.lower()
    matched_role = None
    keywords = {
        "sub_kick": ["sub kick", "subkick", "sub_kick", "sub bd", "sub_bd", "sub-bd", "subbd"],
        "kick": ["kick", "bd", "bass drum"],
        "snare_roll": ["snare roll", "snareroll", "snare_roll", "roll"],
        "snare": ["snare", "sd"],
        "rim": ["rim", "rimshot", "rim_shot", "side stick", "sidestick"],
        "clap": ["clap", "cp"],
        "open_hat": ["open hat", "oh", "open hihat", "op_hat", "open_hat", "open_hihat", "open hi-hat", "open hi hat", "open-hat"],
        "closed_hat": ["closed hat", "ch", "closed hihat", "cl_hat", "closed_hat", "closed_hihat", "close_hat", "hihat", "hat", "hhat", "closed hi-hat", "closed hi hat", "closed-hat"],
        "perc": ["perc", "shaker", "cowbell", "conga", "bongo", "tambourine", "clave"],
        "tom": ["tom", "floor tom", "rack tom", "low tom", "mid tom", "high tom"],
        "crash": ["crash", "cr"],
        "ride": ["ride", "rd"],
        "cymbal": ["cymbal", "splash", "china", "cym"],
        "fx": ["fx", "sfx", "laser", "sweep", "noise"],
        "shot": ["shot", "hit"],
        "synth": ["synth", "synthesizer"],
        "arp": ["arp", "arpeggiator"],
        "stab": ["stab", "chord stab"],
        "loop": ["loop"],
        "texture": ["texture", "atmos", "drone"],
        "vocal": ["vox", "vocal", "shout", "scream", "choir", "voice"],
        "bass": ["bass", "sub"]
    }
    for r_name, kws in keywords.items():
        matched_kw = False
        for k in kws:
            if len(k) <= 2:
                if re.search(r'(?:\b|_)' + re.escape(k) + r'(?:\b|_)', lbl_lower):
                    matched_kw = True
                    break
            else:
                if k in lbl_lower:
                    matched_kw = True
                    break
        if matched_kw:
            if r_name == "closed_hat" and "open" in lbl_lower:
                continue
            matched_role = r_name
            break

    if matched_role:
        role_val = matched_role
    else:
        role_val = "unknown_pad"

    drum_core_roles = {
        "kick", "sub_kick", "snare", "snare_roll", "clap", "rim", 
        "closed_hat", "open_hat", "hat", "perc", "tom", 
        "cymbal", "crash", "ride"
    }
    performance_pad_roles = {"fx", "shot", "synth", "arp", "stab", "loop", "texture", "vocal", "bass"}

    if role_val in drum_core_roles:
        group = "drum_core"
    elif role_val in performance_pad_roles:
        group = "performance_pad"
    else:
        group = "unknown_pad"

    return group, role_val, (0.90 if role_val != "unknown_pad" else 0.0)


def build_kit_profile(path: str | Path) -> dict:
    path = Path(path)
    pads = extract_drum_pads_loose(path)
    notes = [int(p["note"]) for p in pads]

    kit_name = path.stem
    root = read_ableton_xml(path)
    chain_info = inspect_kit_device_chains(root, path)

    pads_dict = {str(p["note"]): p for p in pads}
    for note_str, p in pads_dict.items():
        group, role, conf = classify_pad_by_label(p.get("label") or "")
        p["pad_semantic_group"] = group
        p["normalized_role"] = role
        p["confidence"] = conf

        summary = chain_info["device_chain_summary"].get(note_str, {})
        p["device_profile"] = summary.get("device_profile")
        p["sonic_hints"] = summary.get("sonic_hints")
        p["sample_files"] = summary.get("sample_files", [])
        p["primary_sample"] = summary.get("primary_sample")

    for note_str, summary in chain_info["device_chain_summary"].items():
        if note_str in pads_dict:
            p = pads_dict[note_str]
            p["has_simpler"] = summary["has_simpler"]
            p["has_sampler"] = summary["has_sampler"]
            p["effects"] = summary["effects"]
            p["chain_complexity"] = summary["chain_complexity"]
            if p["normalized_role"] == "unknown_pad" and summary["normalized_role"] != "unknown_pad":
                p["pad_semantic_group"] = summary["semantic_group"]
                p["normalized_role"] = summary["normalized_role"]
                p["confidence"] = summary["confidence"]

    agg_fields = aggregate_kit_level_fields(pads_dict)

    return {
        "type": "kit_profile",
        "kit_id": slugify(kit_name),
        "kit_name": kit_name,
        "source_file": str(path),
        "pad_count": len(pads),
        "note_range": [min(notes), max(notes)] if notes else None,
        "mpe_like": infer_mpe_like(pads),
        "pads": pads_dict,
        **chain_info,
        **agg_fields
    }


def _xml_values(root, tag: str) -> list[str]:
    out = []
    for elem in root.iter(tag):
        val = elem.attrib.get("Value") or (elem.text or "").strip()
        if val:
            out.append(val)
    return out


def extract_clip_boundaries(root) -> dict:
    """Extract timing boundaries from the first MidiClip scope only."""
    clip = next(root.iter("MidiClip"), None)
    if clip is None:
        return {
            "loop_start": None, "loop_end": None, "loop_length": None,
            "start_marker": None, "end_marker": None,
            "source": "missing_midi_clip",
        }

    def first_float(*tags):
        for tag in tags:
            element = next(clip.iter(tag), None)
            if element is None:
                continue
            raw = element.attrib.get("Value") or (element.text or "").strip()
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    loop_start = first_float("LoopStart")
    loop_end = first_float("LoopEnd")
    loop_length = (
        loop_end - loop_start
        if loop_start is not None and loop_end is not None and loop_end > loop_start
        else None
    )
    return {
        "loop_start": loop_start,
        "loop_end": loop_end,
        "loop_length": loop_length,
        "start_marker": first_float("StartMarker", "CurrentStart"),
        "end_marker": first_float("EndMarker", "CurrentEnd"),
        "source": "scoped_midi_clip",
    }


def inspect_clip_note_structure(path: str | Path) -> list[dict]:
    path = Path(path)
    root = read_ableton_xml(path)
    events = []

    def walk(elem, ancestors: tuple[str, ...] = ()):
        current = ancestors + (elem.tag,)
        if "MidiClip" in current and "Notes" in current and "KeyTracks" in current and "KeyTrack" in current:
            for child in list(elem):
                if child.tag in {"MidiKey", "Note", "Key"}:
                    val = child.attrib.get("Value") or (child.text or "").strip()
                    if val and val.replace(".", "", 1).isdigit():
                        events.append({
                            "path": "/".join(current + (child.tag,)),
                            "tag": child.tag,
                            "value": int(float(val)),
                        })
                elif child.tag in {"Velocity", "NoteVelocity"}:
                    val = child.attrib.get("Value") or (child.text or "").strip()
                    if val and val.replace(".", "", 1).isdigit():
                        events.append({
                            "path": "/".join(current + (child.tag,)),
                            "tag": child.tag,
                            "value": float(val),
                        })
        for child in list(elem):
            walk(child, current)

    walk(root)
    return events


def extract_clip_events(root) -> list[dict]:
    events: list[dict] = []

    for key_track in root.iter("KeyTrack"):
        midi_key_elem = key_track.find("MidiKey")
        if midi_key_elem is None:
            continue

        midi_key_raw = midi_key_elem.attrib.get("Value") or (midi_key_elem.text or "").strip()
        try:
            note = int(float(midi_key_raw))
        except (TypeError, ValueError):
            continue

        notes_elem = key_track.find("Notes")
        if notes_elem is None:
            continue

        for midi_note_event in notes_elem.iter("MidiNoteEvent"):
            beat_raw = midi_note_event.attrib.get("Time")
            velocity_raw = midi_note_event.attrib.get("Velocity")
            duration_raw = midi_note_event.attrib.get("Duration")
            if beat_raw in (None, "") or velocity_raw in (None, ""):
                continue

            try:
                beat = float(beat_raw)
                velocity = int(float(velocity_raw))
                duration = float(duration_raw) if duration_raw else 0.25
            except (TypeError, ValueError):
                continue

            events.append({
                "note": note,
                "beat": beat,
                "velocity": velocity,
                "duration": duration,
            })

    return sorted(events, key=lambda ev: (ev["beat"], ev["note"]))


def inspect_alc_embedded_kit(path: str | Path) -> dict:
    path = Path(path)
    root = read_ableton_xml(path)
    pads: dict[int, dict] = {}

    def _label_for_branch(branch_elem) -> str:
        for child in branch_elem.iter():
            if child.tag in {"Name", "UserName"}:
                value = (child.attrib.get("Value") or (child.text or "")).strip()
                if value:
                    return value
        return ""

    for branch in root.iter("DrumBranch"):
        receiving = None
        sending = None
        label = ""

        for child in list(branch):
            if child.tag == "BranchInfo":
                for info_child in list(child):
                    if info_child.tag == "ReceivingNote":
                        receiving = (info_child.attrib.get("Value") or (info_child.text or "")).strip()
                    elif info_child.tag == "SendingNote":
                        sending = (info_child.attrib.get("Value") or (info_child.text or "")).strip()
            elif child.tag in {"Name", "UserName"}:
                label = (child.attrib.get("Value") or (child.text or "")).strip()

        if not receiving:
            for child in branch.iter():
                if child.tag == "ReceivingNote":
                    receiving = (child.attrib.get("Value") or (child.text or "")).strip()
                    break

        if receiving is None:
            continue

        try:
            note = int(float(receiving))
        except (TypeError, ValueError):
            continue

        if not label:
            label = _label_for_branch(branch) or f"Note {note}"

        choke_group = None
        for child in branch.iter("ChokeGroup"):
            val = child.attrib.get("Value")
            if val and val != "0":
                try:
                    choke_group = int(val)
                except ValueError:
                    pass
                break

        if note not in pads:
            pads[note] = {
                "note": note,
                "label": label,
                "receiving_note": receiving,
                "sending_note": sending,
                "sample_names": [label] if label else [],
                "choke_group": choke_group,
            }

    chain_info = inspect_kit_device_chains(root, path)
    pads_dict = {str(note): pad for note, pad in sorted(pads.items())}

    for note_str, p in pads_dict.items():
        group, role, conf = classify_pad_by_label(p.get("label") or "")
        p["pad_semantic_group"] = group
        p["normalized_role"] = role
        p["confidence"] = conf

        summary = chain_info["device_chain_summary"].get(note_str, {})
        p["device_profile"] = summary.get("device_profile")
        p["sonic_hints"] = summary.get("sonic_hints")
        p["sample_files"] = summary.get("sample_files", [])
        p["primary_sample"] = summary.get("primary_sample")

    for note_str, summary in chain_info["device_chain_summary"].items():
        if note_str in pads_dict:
            p = pads_dict[note_str]
            p["has_simpler"] = summary["has_simpler"]
            p["has_sampler"] = summary["has_sampler"]
            p["effects"] = summary["effects"]
            p["chain_complexity"] = summary["chain_complexity"]
            if p["normalized_role"] == "unknown_pad" and summary["normalized_role"] != "unknown_pad":
                p["pad_semantic_group"] = summary["semantic_group"]
                p["normalized_role"] = summary["normalized_role"]
                p["confidence"] = summary["confidence"]

    agg_fields = aggregate_kit_level_fields(pads_dict)

    return {
        "type": "embedded_kit",
        "source_file": str(path),
        "pad_count": len(pads),
        "pads": pads_dict,
        **chain_info,
        **agg_fields
    }


def inspect_alc_clip(path: str | Path) -> dict:
    path = Path(path)
    root = read_ableton_xml(path)

    tempos = _xml_values(root, "Tempo")
    loop_start = _xml_values(root, "LoopStart")
    loop_end = _xml_values(root, "LoopEnd")
    current_start = _xml_values(root, "CurrentStart")
    current_end = _xml_values(root, "CurrentEnd")
    clip_boundaries = extract_clip_boundaries(root)

    events = extract_clip_events(root)
    if events:
        note_values = [int(event["note"]) for event in events]
        velocity_values = [float(event["velocity"]) for event in events]
        note_event_path = "LiveSet/Tracks/MidiTrack/DeviceChain/MainSequencer/ClipSlotList/.../MidiClip/Notes/KeyTracks/KeyTrack/Notes/MidiNoteEvent"
        events_source = "midi_note_event"
    else:
        note_events = inspect_clip_note_structure(path)
        note_values = [event["value"] for event in note_events if isinstance(event["value"], int)]
        velocity_values = [event["value"] for event in note_events if isinstance(event["value"], float)]
        events = [
            {"note": int(note), "beat": float(index), "velocity": 100}
            for index, note in enumerate(note_values)
        ]
        note_event_path = note_events[0]["path"] if note_events else None
        events_source = "fallback_synthetic"

    notes_used = sorted(set(note_values))

    return {
        "type": "clip_profile",
        "clip_id": slugify(path.stem),
        "source_file": str(path),
        "bpm_candidates": tempos,
        "loop_start": loop_start[:3],
        "loop_end": loop_end[:3],
        "current_start": current_start[:3],
        "current_end": current_end[:3],
        "clip_boundaries": clip_boundaries,
        "event_count_guess": len(note_values),
        "notes_used": notes_used,
        "events": events,
        "events_source": events_source,
        "note_event_path": note_event_path,
        "note_range": [min(notes_used), max(notes_used)] if notes_used else None,
        "velocity_min": min(velocity_values) if velocity_values else None,
        "velocity_max": max(velocity_values) if velocity_values else None,
        "velocity_avg": round(sum(velocity_values) / len(velocity_values), 3) if velocity_values else None,
        "mpe_like": bool(notes_used and min(notes_used) >= 70 and max(notes_used) <= 100),
    }


def write_profile(profile: dict, out_dir: str | Path = "data/profiles") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    name = profile.get("kit_id") or profile.get("clip_id") or "profile"
    out = out_dir / f"{name}.json"
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return out
