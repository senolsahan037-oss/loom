"""Get a decoded WAV out of a YouTube URL (or any local audio/video file)."""

import json
import os
import re
import shutil
import subprocess
import unicodedata

SAMPLE_RATE = 44100


class FetchError(RuntimeError):
    pass


def require(binary):
    path = shutil.which(binary)
    if not path:
        raise FetchError(f"'{binary}' not found on PATH. Install it (brew install {binary}).")
    return path


def is_url(source):
    return bool(re.match(r"^https?://", source.strip(), re.I))


def parse_timestamp(value):
    """'83' | '1:23' | '1:23.5' | '01:02:03' -> seconds (float)."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad timestamp: {value}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def slugify(text, fallback="sample"):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60].strip("-") or fallback


def download(url, workdir, quiet=False):
    """yt-dlp -> (path to the downloaded audio stream, metadata dict)."""
    require("yt-dlp")
    template = os.path.join(workdir, "download.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestaudio/best",
        "--no-playlist",
        "--write-info-json",
        "--no-progress" if quiet else "--newline",
        "-o", template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FetchError(f"yt-dlp failed:\n{proc.stderr.strip()[-2000:]}")

    info_path = os.path.join(workdir, "download.info.json")
    meta = {}
    if os.path.exists(info_path):
        with open(info_path) as handle:
            raw = json.load(handle)
        meta = {
            "title": raw.get("title"),
            "video_id": raw.get("id"),
            "uploader": raw.get("uploader"),
            "duration_s": raw.get("duration"),
            "webpage_url": raw.get("webpage_url") or url,
            "audio_codec": raw.get("acodec"),
            "abr_kbps": raw.get("abr"),
        }

    candidates = [
        os.path.join(workdir, name)
        for name in sorted(os.listdir(workdir))
        if name.startswith("download.") and not name.endswith(".info.json")
    ]
    if not candidates:
        raise FetchError("yt-dlp reported success but produced no audio file.")
    return candidates[0], meta


def probe_duration(path):
    require("ffprobe")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def to_wav(src, dest, start=None, end=None, sample_rate=SAMPLE_RATE):
    """Decode to 32-bit float WAV, optionally trimming to [start, end)."""
    require("ffmpeg")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if start is not None:
        cmd += ["-ss", f"{start:.4f}"]          # after -i: sample-accurate
    if end is not None:
        cmd += ["-to", f"{end:.4f}"]
    cmd += ["-ac", "2", "-ar", str(sample_rate), "-c:a", "pcm_f32le", dest]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FetchError(f"ffmpeg decode failed:\n{proc.stderr.strip()[-2000:]}")
    return dest


def resolve(source, workdir, start=None, end=None, quiet=False):
    """URL or local path -> {'wav', 'meta', 'origin'}."""
    os.makedirs(workdir, exist_ok=True)
    if is_url(source):
        raw, meta = download(source, workdir, quiet=quiet)
        origin = "youtube:yt-dlp"
    else:
        raw = os.path.abspath(os.path.expanduser(source))
        if not os.path.exists(raw):
            raise FetchError(f"file not found: {raw}")
        meta = {
            "title": os.path.splitext(os.path.basename(raw))[0],
            "video_id": None,
            "duration_s": probe_duration(raw),
            "webpage_url": None,
        }
        origin = "local_file"

    wav = os.path.join(workdir, "source.wav")
    to_wav(raw, wav, start=start, end=end)
    meta["trim_start_s"] = start
    meta["trim_end_s"] = end
    return {"wav": wav, "meta": meta, "origin": origin}
