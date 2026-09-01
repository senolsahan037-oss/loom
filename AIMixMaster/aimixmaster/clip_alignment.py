"""Read-only, intra-track audio-clip peak alignment recommendations."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .gain_staging import measure_audio_file
from .project_analyzer import analyze_tracks, value


def _number(element: ET.Element, path: str, default: float = 0.0) -> float:
    try: return float(value(element.find(path), str(default)))
    except ValueError: return default


def _source(clip: ET.Element, als: Path) -> Path | None:
    raw, relative = value(clip.find('.//SampleRef/FileRef/Path')), value(clip.find('.//SampleRef/FileRef/RelativePath'))
    return next((p for p in ([Path(raw)] if raw else []) + ([als.parent / relative] if relative else []) if p.is_file()), None)


def _has_fade(clip: ET.Element) -> bool:
    fades = clip.find('./Fades')
    # Live serialises a Fades container for many clips. Only a non-zero
    # effective in/out duration is an audible fade.
    nodes = list(fades) if fades is not None else []
    nodes += [node for node in (clip.find('./FadeIn'), clip.find('./FadeOut')) if node is not None]
    for node in nodes:
        tag = node.tag.casefold()
        if ('fadein' in tag or 'fadeout' in tag) and ('length' in tag or 'duration' in tag or tag in {'fadein', 'fadeout'}):
            try:
                if float(node.attrib.get('Value', '0')) > 1e-12: return True
            except ValueError: pass
    return False


def _has_clip_automation(clip: ET.Element) -> bool:
    return clip.find('./AutomationEnvelopes/Envelopes/AutomationEnvelope') is not None


def _track_volume_automation(track: ET.Element) -> bool:
    # An empty <Envelopes/> container is Live's default and is not automation.
    # This conservative first pass blocks only an actual track envelope.
    return track.find('./AutomationEnvelopes/Envelopes/AutomationEnvelope') is not None


def analyze_clip_alignment(root: ET.Element, als_path: Path, limit_db: float = 12.0, threshold_db: float = .25) -> dict[str, Any]:
    tracks = []
    cache: dict[Path, dict[str, Any]] = {}
    for info in analyze_tracks(root):
        if info.track_type != 'AudioTrack': continue
        clip_rows = []
        active_windows = []
        clips = sorted(list(info.element.iter('AudioClip')), key=lambda c: (_number(c, './CurrentStart', _number(c, '.', 0)), c.attrib.get('Id', '')))
        for ordinal, clip in enumerate(clips):
            start, end = _number(clip, './CurrentStart'), _number(clip, './CurrentEnd')
            if value(clip.find('./Disabled'), 'false').lower() != 'true': active_windows.append((start, end, ordinal))
        for ordinal, clip in enumerate(clips):
            row: dict[str, Any] = {'clip_id': clip.attrib.get('Id', str(ordinal)), 'timeline_start': _number(clip, './CurrentStart'), 'timeline_end': _number(clip, './CurrentEnd'), 'source_peak_dbfs': None, 'source_rms_dbfs': None, 'current_clip_gain_db': None, 'effective_peak_dbfs': None, 'effective_rms_dbfs': None, 'gain_correction_db': None, 'recommended_clip_gain_db': None, 'status': 'preserve_unresolved', 'reason': ''}
            if value(clip.find('./Disabled'), 'false').lower() == 'true': row.update(status='preserve_unresolved', reason='clip_deactivated'); clip_rows.append(row); continue
            path = _source(clip, als_path)
            if path is None: row.update(status='preserve_missing_source', reason='source_audio_missing'); clip_rows.append(row); continue
            metrics = cache.setdefault(path, measure_audio_file(path, include_lufs=False))
            if metrics.get('peak') is None or metrics.get('rms') is None: row.update(status='preserve_unresolved', reason='invalid_source_measurement'); clip_rows.append(row); continue
            gain_linear = _number(clip, './SampleVolume', 1.0)
            if gain_linear <= 0: row.update(status='preserve_unresolved', reason='nonpositive_clip_gain'); clip_rows.append(row); continue
            gain = 20 * math.log10(gain_linear)
            row.update(source_peak_dbfs=metrics['peak'], source_rms_dbfs=metrics['rms'], current_clip_gain_db=round(gain, 6), effective_peak_dbfs=round(float(metrics['peak']) + gain, 6), effective_rms_dbfs=round(float(metrics['rms']) + gain, 6))
            if _has_clip_automation(clip): row.update(status='preserve_volume_automation', reason='clip_volume_automation'); clip_rows.append(row); continue
            if _has_fade(clip): row.update(status='preserve_fade', reason='clip_fade_present'); clip_rows.append(row); continue
            overlap = any(a < row['timeline_end'] and row['timeline_start'] < b for a,b,i in active_windows if i != ordinal)
            if overlap: row.update(status='preserve_overlap', reason='active_clip_overlap'); clip_rows.append(row); continue
            row.update(status='eligible', reason='eligible_for_alignment'); clip_rows.append(row)
        track_auto = _track_volume_automation(info.element)
        eligible = [] if track_auto else [r for r in clip_rows if r['status'] == 'eligible']
        reference = eligible[0] if eligible else None
        if track_auto:
            for row in clip_rows:
                if row['status'] == 'eligible': row.update(status='preserve_volume_automation', reason='track_volume_automation')
        if reference:
            reference.update(status='reference', reason='first_eligible_timeline_clip')
            for row in eligible[1:]:
                correction = round(float(reference['effective_peak_dbfs']) - float(row['effective_peak_dbfs']), 6)
                if abs(correction) < threshold_db: row.update(status='no_change', gain_correction_db=correction, recommended_clip_gain_db=row['current_clip_gain_db'], reason='within_no_change_threshold')
                elif abs(correction) > limit_db: row.update(status='preserve_out_of_range', gain_correction_db=correction, reason='correction_exceeds_safe_limit')
                else: row.update(status='alignment_recommended', gain_correction_db=correction, recommended_clip_gain_db=round(float(row['current_clip_gain_db']) + correction, 6), reason='aligned_to_first_eligible_clip')
        tracks.append({'track_id': info.track_id, 'track': info.name, 'track_volume_automation': track_auto, 'reference_clip_id': reference['clip_id'] if reference else None, 'reference_effective_peak_dbfs': reference['effective_peak_dbfs'] if reference else None, 'clips': clip_rows})
    all_clips = [c for t in tracks for c in t['clips']]
    corrections = [c['gain_correction_db'] for c in all_clips if isinstance(c['gain_correction_db'], float)]
    counts = {s: sum(c['status'] == s for c in all_clips) for s in sorted({c['status'] for c in all_clips})}
    return {'schema_version':'2.0', 'mode':'dry_run', 'tracks':tracks, 'summary': {'alignable_tracks':sum(t['reference_clip_id'] is not None for t in tracks), 'alignment_recommendations':counts.get('alignment_recommended',0), 'preserved_clips':sum(v for k,v in counts.items() if k.startswith('preserve_')), 'status_distribution':counts, 'minimum_correction_db':min(corrections) if corrections else None, 'maximum_correction_db':max(corrections) if corrections else None, 'tracks_without_reference':[t['track'] for t in tracks if t['reference_clip_id'] is None]}}

def markdown_clip_alignment(report: dict[str, Any]) -> str:
    s = report['summary']; lines = ['# Clip Alignment Dry Run', '', f"Alignable tracks: {s['alignable_tracks']}", f"Recommendations: {s['alignment_recommendations']}", f"Preserved clips: {s['preserved_clips']}", '', '| Track | Reference | Clip | Status | Correction dB |', '|---|---|---|---|---:|']
    for track in report['tracks']:
        for clip in track['clips']:
            lines.append(f"| {track['track']} | {track['reference_clip_id'] or 'none'} | {clip['clip_id']} | {clip['status']} | {clip['gain_correction_db'] if clip['gain_correction_db'] is not None else '—'} |")
    return '\n'.join(lines) + '\n'
