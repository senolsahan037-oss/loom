import os, json, re
from pathlib import Path

roots = [
    Path.home() / "Documents",
    Path.home() / "Music",
    Path.home() / "Library/Application Support",
    Path.home() / "Library/Audio",
    Path("/Library/Application Support"),
    Path("/Library/Audio")
]

preset_exts = {
    ".vital": "Vital",
    ".vitalbank": "Vital",
    ".srgpreset": "Surge XT",
    ".fxp": "Generic Plugin",
    ".fxb": "Generic Plugin",
    ".syx": "Dexed",
    ".h2p": "u-he",
    ".h2pbank": "u-he",
    ".talpreset": "TAL",
    ".tunobank": "TAL"
}

plugin_keywords = [
    "vital", "surge", "dexed", "tal", "u-he", "uhe",
    "zebra", "tyrell", "bassline", "noisemaker", "j-8"
]

def clean_name(name):
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()

def guess_plugin(path, ext):
    text = str(path).lower()
    if "vital" in text:
        return "Vital"
    if "surge" in text:
        return "Surge XT"
    if "dexed" in text:
        return "Dexed"
    if "zebra" in text:
        return "u-he Zebra"
    if "tyrell" in text:
        return "u-he TyrellN6"
    if "tal" in text or "bassline" in text or "noisemaker" in text:
        return "TAL"
    return preset_exts.get(ext, "Unknown Plugin")

def guess_category(name):
    n = name.lower()
    if any(k in n for k in ["bass", "sub", "808"]):
        return "bass"
    if any(k in n for k in ["lead", "solo"]):
        return "lead"
    if any(k in n for k in ["pad", "atmo", "ambient", "texture"]):
        return "pad_atmosphere"
    if any(k in n for k in ["pluck", "arp", "seq"]):
        return "melodic_sequence"
    if any(k in n for k in ["key", "piano", "rhodes", "organ"]):
        return "keys"
    if any(k in n for k in ["drum", "perc", "kick", "snare", "hat"]):
        return "drums"
    if any(k in n for k in ["fx", "riser", "impact", "noise"]):
        return "fx"
    return "unknown"

items = []

for root in roots:
    if not root.exists():
        continue

    for dirpath, dirnames, filenames in os.walk(root):
        low = dirpath.lower()
        if any(skip in low for skip in ["node_modules", ".git", "cache", "caches", "trash"]):
            continue

        if not any(k in low for k in plugin_keywords):
            continue

        for filename in filenames:
            path = Path(dirpath) / filename
            ext = path.suffix.lower()

            if ext not in preset_exts:
                continue

            preset_name = clean_name(path.stem)
            plugin_name = guess_plugin(path, ext)

            items.append({
                "plugin_name": plugin_name,
                "preset_name": preset_name,
                "path": str(path),
                "extension": ext,
                "bank_name": clean_name(Path(dirpath).name),
                "category_guess": guess_category(preset_name),
                "character_keywords": [],
                "source": "Plugin Bank"
            })

out_dir = Path.home() / "Library/Application Support/ArrangementGPS"
out_dir.mkdir(parents=True, exist_ok=True)

out_path = out_dir / "plugin_bank_index.json"
out_path.write_text(json.dumps({
    "total_items": len(items),
    "items": items
}, indent=2, ensure_ascii=False))

print(f"Indexed {len(items)} plugin bank presets")
print(f"Saved: {out_path}")
