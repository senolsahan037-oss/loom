"""Build Genre Profiles from well-known released masters.

The track list lives in ``data/reference_tracks.json``. For every track the
builder searches YouTube with yt-dlp, prefers the label's auto-generated
"Topic" upload (the released master itself, no video intro or dialogue),
downloads the audio to a temporary WAV, measures it with the same engine the
service and the MCP use, and deletes the audio. The catalog keeps only the
aggregate distributions plus this provenance: artist, title, year, video id,
channel, duration and how the upload was matched.

Usage::

    python3 -m subverse_mix.reference_profiles \
        --catalog subverse_mix/data/genre_profiles.json \
        --genres hiphop trap electronic pop rock \
        --work-dir /tmp/reference-profiles

The build is resumable: resolved video ids and per-genre completion are kept
in ``<work-dir>/state.json``; ``--inspect`` resolves and prints the matches
without downloading anything.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .genre_profiles import (
    GenreProfileError,
    GenreProfileStore,
    MIN_PROFILE_SOURCE_COUNT,
    build_genre_profile,
)

DEFAULT_TRACKS_PATH = Path(__file__).with_name("data") / "reference_tracks.json"
SEARCH_RESULTS = 8
MIN_DURATION_SECONDS = 90.0
MAX_DURATION_SECONDS = 720.0

# Uploads that are not the released master.
REJECT_TITLE_TOKENS = (
    "lyric", "lyrics", "remix", "live", "cover", "karaoke", "instrumental",
    "sped up", "slowed", "nightcore", "8d", "reverb", "acoustic", "acapella",
    "reaction", "tutorial", "mashup", "extended", "radio edit", "clean version",
    "edit)", "loop", "1 hour", "hour version", "trailer", "teaser", "behind the",
    "making of", "dance video", "choreography", "piano", "orchestral",
)
REJECT_CHANNEL_TOKENS = (
    "lyrics", "lyric", "7clouds", "cakes & eclairs", "taz network", "spotlight",
    "vibes", "nightcore", "karaoke", "reaction", "cover", "tribute",
)


class ReferenceProfileError(RuntimeError):
    pass


def _normalize(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def load_track_list(path: Path = DEFAULT_TRACKS_PATH) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("genres"), list):
        raise ReferenceProfileError("Reference track list is malformed.")
    return document


def _run_yt_dlp(args: List[str], timeout: int = 180) -> subprocess.CompletedProcess:
    if shutil.which("yt-dlp") is None:
        raise ReferenceProfileError("yt-dlp is not installed.")
    return subprocess.run(
        ["yt-dlp", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def search_candidates(artist: str, title: str) -> List[Dict[str, Any]]:
    """Flat YouTube search; returns entries with id, title, channel, duration."""
    query = f"ytsearch{SEARCH_RESULTS}:{artist} {title}"
    result = _run_yt_dlp(["--flat-playlist", "-J", "--no-warnings", query])
    if result.returncode != 0 or not result.stdout.strip():
        raise ReferenceProfileError(
            f"YouTube search failed for {artist} - {title}: {result.stderr.strip()[:200]}"
        )
    payload = json.loads(result.stdout)
    entries = []
    for entry in payload.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        entries.append(
            {
                "id": str(entry["id"]),
                "title": str(entry.get("title") or ""),
                "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                "duration": entry.get("duration"),
            }
        )
    return entries


def rank_candidates(
    artist: str,
    title: str,
    candidates: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Order search results so the released master comes first.

    Ranking (highest first):
      3  auto-generated "Topic" style upload: title is exactly the song title
         and the channel is the artist (yt-dlp's flat search drops the
         " - Topic" suffix, full extraction restores it);
      2  the artist's channel with "official audio" in the title;
      1  the artist's channel (official video: the master with picture);
      0  anything else that is not rejected.
    Lyric channels, remixes, live, covers, sped-up/slowed uploads and wrong
    durations are rejected outright.
    """
    artist_key = _normalize(artist)
    title_key = _normalize(title)
    ranked: List[Dict[str, Any]] = []
    for candidate in candidates:
        raw_title = candidate["title"]
        channel = candidate["channel"]
        duration = candidate.get("duration")
        lowered = raw_title.casefold()
        if any(token in lowered for token in REJECT_TITLE_TOKENS):
            continue
        if any(token in channel.casefold() for token in REJECT_CHANNEL_TOKENS):
            continue
        if duration is not None and not MIN_DURATION_SECONDS <= float(duration) <= MAX_DURATION_SECONDS:
            continue
        cand_title = _normalize(raw_title)
        cand_channel = _normalize(channel)
        if title_key not in cand_title and cand_title not in title_key:
            continue
        # "SnoopDoggTV", "EminemMusic": compare without spaces as well.
        artist_compact = artist_key.replace(" ", "")
        channel_compact = cand_channel.replace(" ", "")
        artist_channel = (
            artist_key in cand_channel
            or cand_channel in artist_key
            or (len(artist_compact) >= 4 and artist_compact in channel_compact)
        )
        artist_in_title = artist_key in cand_title
        if not (artist_channel or artist_in_title):
            continue
        if cand_title == title_key and artist_channel:
            score, kind = 3, "topic_or_artist_audio"
        elif artist_channel and "official audio" in lowered:
            score, kind = 2, "official_audio"
        elif artist_channel:
            score, kind = 1, "artist_channel"
        else:
            score, kind = 0, "other"
        ranked.append({**candidate, "score": score, "match_kind": kind})
    ranked.sort(key=lambda item: -item["score"])
    return ranked


MIN_ACCEPTED_SCORE = 1  # the artist's own channel or the label's Topic upload


def resolve_track(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """Find the released master's upload; a random re-upload is never accepted."""
    candidates = rank_candidates(artist, title, search_candidates(artist, title))
    if not candidates or candidates[0]["score"] < MIN_ACCEPTED_SCORE:
        # Second try aimed at the label's auto-generated upload.
        fallback = rank_candidates(
            artist, title, search_candidates(f"{artist} - Topic", title)
        )
        candidates = sorted(candidates + fallback, key=lambda item: -item["score"])
    candidates = [item for item in candidates if item["score"] >= MIN_ACCEPTED_SCORE]
    if not candidates:
        return None
    chosen = candidates[0]
    # Full extraction confirms the channel (" - Topic") and duration.
    result = _run_yt_dlp(
        ["-J", "--no-warnings", "--no-playlist", f"https://www.youtube.com/watch?v={chosen['id']}"]
    )
    if result.returncode == 0 and result.stdout.strip():
        info = json.loads(result.stdout)
        chosen["channel"] = str(info.get("channel") or chosen["channel"])
        chosen["duration"] = info.get("duration", chosen.get("duration"))
        chosen["title"] = str(info.get("title") or chosen["title"])
        if chosen["channel"].endswith(" - Topic"):
            chosen["match_kind"] = "topic"
    return chosen


def download_wav(video_id: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    template = str(destination.with_suffix(""))
    result = _run_yt_dlp(
        [
            "-f", "bestaudio/best",
            "-x", "--audio-format", "wav",
            "--no-playlist", "--no-warnings", "--no-progress",
            "--match-filter", f"duration < {int(MAX_DURATION_SECONDS)}",
            "-o", f"{template}.%(ext)s",
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        timeout=600,
    )
    output = destination.with_suffix(".wav")
    if result.returncode != 0 or not output.exists():
        raise ReferenceProfileError(
            f"Download failed for {video_id}: {result.stderr.strip()[:300]}"
        )
    return output


def _load_state(path: Path) -> Dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"resolved": {}, "completed_genres": []}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def build_reference_profiles(
    catalog_path: Path,
    genres: Iterable[str],
    work_dir: Path,
    tracks_path: Path = DEFAULT_TRACKS_PATH,
    inspect_only: bool = False,
    min_sources: int = 12,
) -> None:
    document = load_track_list(tracks_path)
    by_id = {genre["id"]: genre for genre in document["genres"]}
    state_path = work_dir / "state.json"
    state = _load_state(state_path)
    store = None if inspect_only else GenreProfileStore(catalog_path)

    for genre_id in genres:
        genre = by_id.get(genre_id)
        if genre is None:
            raise ReferenceProfileError(f"Unknown genre '{genre_id}'.")
        if genre_id in state["completed_genres"] and not inspect_only:
            _log(f"{genre_id}: already built, skipping")
            continue
        _log(f"=== {genre['name']} ({len(genre['tracks'])} tracks) ===")
        sources: List[Dict[str, Any]] = []
        paths: List[Path] = []
        for track in genre["tracks"]:
            key = f"{track['artist']} - {track['title']}"
            resolved = state["resolved"].get(key)
            if resolved is not None and resolved.get("match_kind") == "other":
                resolved = None  # cached before the acceptance threshold existed
            if resolved is None:
                try:
                    resolved = resolve_track(track["artist"], track["title"])
                except ReferenceProfileError as exc:
                    _log(f"  !! {key}: {exc}")
                    resolved = None
                state["resolved"][key] = resolved or {"unresolved": True}
                _save_state(state_path, state)
            if not resolved or resolved.get("unresolved"):
                _log(f"  -- {key}: no acceptable upload found")
                continue
            _log(
                f"  {key} -> {resolved['id']} [{resolved.get('match_kind')}] "
                f"{resolved.get('channel')} {resolved.get('duration')}s"
            )
            if inspect_only:
                continue
            wav_path = work_dir / genre_id / f"{resolved['id']}.wav"
            try:
                if not wav_path.exists():
                    download_wav(resolved["id"], wav_path)
            except ReferenceProfileError as exc:
                _log(f"  !! {key}: {exc}")
                continue
            paths.append(wav_path)
            sources.append(
                {
                    "artist": track["artist"],
                    "title": track["title"],
                    "year": track.get("year"),
                    "video_id": resolved["id"],
                    "video_url": f"https://www.youtube.com/watch?v={resolved['id']}",
                    "channel": resolved.get("channel"),
                    "upload_title": resolved.get("title"),
                    "duration_seconds": resolved.get("duration"),
                    "match_kind": resolved.get("match_kind"),
                }
            )
        if inspect_only:
            continue
        if len(paths) < max(min_sources, MIN_PROFILE_SOURCE_COUNT):
            raise ReferenceProfileError(
                f"{genre_id}: only {len(paths)} sources downloaded; need {min_sources}."
            )
        _log(f"{genre_id}: measuring {len(paths)} masters")
        valid_paths: List[Path] = []
        valid_sources: List[Dict[str, Any]] = []
        for path, source in zip(paths, sources):
            try:
                from .mix_analyzer import extract_mix_features

                extract_mix_features(path, path.name)
            except Exception as exc:  # noqa: BLE001 - one bad decode must not kill the build
                _log(f"  !! {source['artist']} - {source['title']}: undecodable ({exc})")
                continue
            valid_paths.append(path)
            valid_sources.append(source)
        profile = build_genre_profile(genre_id, genre["name"], valid_paths)
        profile["provenance"] = {
            "provider": "YouTube (artist or label uploads), measured locally",
            "source_note": (
                "YouTube serves an Opus/AAC transcode of the released master; "
                "levels and one-third-octave shape below 16 kHz survive the "
                "transcode, the top band and true-peak detail do not."
            ),
            "selection": document.get("selection"),
            "audio_retained": False,
            "sources": valid_sources,
        }
        assert store is not None
        store.upsert(profile)
        for path in valid_paths:
            path.unlink(missing_ok=True)
        shutil.rmtree(work_dir / genre_id, ignore_errors=True)
        state["completed_genres"].append(genre_id)
        _save_state(state_path, state)
        _log(f"{genre_id}: profile written with {len(valid_paths)} sources; audio deleted")


POOLED_PROFILE_ID = "released"
POOLED_PROFILE_NAME = "Released masters (all genres)"


def build_pooled_profile(
    catalog_path: Path,
    genres: Iterable[str],
    work_dir: Path,
    tracks_path: Path = DEFAULT_TRACKS_PATH,
    min_sources: int = 40,
) -> None:
    """One profile over every reference master of every genre.

    The engine falls back to this profile when no single genre profile is
    clearly nearest: the track is then compared with released masters in
    general instead of with a genre it may not belong to. Genre profiles stay
    untouched; this one carries ``role: pooled`` and is excluded from the
    nearest-genre ranking.
    """
    document = load_track_list(tracks_path)
    by_id = {genre["id"]: genre for genre in document["genres"]}
    state_path = work_dir / "state.json"
    state = _load_state(state_path)
    store = GenreProfileStore(catalog_path)
    paths: List[Path] = []
    sources: List[Dict[str, Any]] = []
    pooled_dir = work_dir / POOLED_PROFILE_ID
    for genre_id in genres:
        genre = by_id[genre_id]
        _log(f"=== pooled: {genre['name']} ===")
        for track in genre["tracks"]:
            key = f"{track['artist']} - {track['title']}"
            resolved = state["resolved"].get(key)
            if resolved is None or resolved.get("match_kind") == "other":
                try:
                    resolved = resolve_track(track["artist"], track["title"])
                except ReferenceProfileError as exc:
                    _log(f"  !! {key}: {exc}")
                    resolved = None
                state["resolved"][key] = resolved or {"unresolved": True}
                _save_state(state_path, state)
            if not resolved or resolved.get("unresolved"):
                continue
            wav_path = pooled_dir / f"{resolved['id']}.wav"
            try:
                if not wav_path.exists():
                    download_wav(resolved["id"], wav_path)
            except ReferenceProfileError as exc:
                _log(f"  !! {key}: {exc}")
                continue
            try:
                from .mix_analyzer import extract_mix_features

                extract_mix_features(wav_path, wav_path.name)
            except Exception as exc:  # noqa: BLE001
                _log(f"  !! {key}: undecodable ({exc})")
                continue
            paths.append(wav_path)
            sources.append({"genre": genre_id, "artist": track["artist"], "title": track["title"], "video_id": resolved["id"]})
            _log(f"  {key} ok ({len(paths)})")
    if len(paths) < max(min_sources, MIN_PROFILE_SOURCE_COUNT):
        raise ReferenceProfileError(f"pooled: only {len(paths)} sources; need {min_sources}.")
    _log(f"pooled: measuring {len(paths)} masters")
    profile = build_genre_profile(POOLED_PROFILE_ID, POOLED_PROFILE_NAME, paths)
    profile["role"] = "pooled"
    profile["provenance"] = {
        "provider": "YouTube (artist or label uploads), measured locally",
        "selection": "Every reference master of every genre profile in this catalog, pooled.",
        "audio_retained": False,
        "genres": sorted({source["genre"] for source in sources}),
        "sources": sources,
    }
    store.upsert(profile)
    shutil.rmtree(pooled_dir, ignore_errors=True)
    _log(f"pooled: profile written with {len(paths)} sources; audio deleted")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--genres", nargs="+", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--tracks", type=Path, default=DEFAULT_TRACKS_PATH)
    parser.add_argument("--min-sources", type=int, default=12)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument(
        "--pooled", action="store_true",
        help="build only the pooled released-masters profile over the given genres",
    )
    args = parser.parse_args(argv)
    try:
        if args.pooled:
            build_pooled_profile(args.catalog, args.genres, args.work_dir, tracks_path=args.tracks)
            return 0
        build_reference_profiles(
            args.catalog,
            args.genres,
            args.work_dir,
            tracks_path=args.tracks,
            inspect_only=args.inspect,
            min_sources=args.min_sources,
        )
    except (ReferenceProfileError, GenreProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
