import os, json
from pathlib import Path

roots = [
    Path("/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library"),
    Path.home() / "Music/Ableton",
    Path.home() / "Library/Application Support/Ableton"
]

exts = {
    ".adg": "rack",
    ".adv": "preset",
    ".alc": "clip",
    ".amxd": "max_device"
}

items = []

for root in roots:
    if not root.exists():
        continue

    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            path = Path(dirpath) / filename
            ext = path.suffix.lower()
            if ext not in exts:
                continue

            parts = [p.lower() for p in path.parts]
            name = path.stem
            lname = name.lower()

            category = "unknown"
            if "drums" in parts or "drum" in lname or "kit" in lname:
                category = "drums"
            elif "bass" in parts or "bass" in lname or "808" in lname:
                category = "bass"
            elif "instruments" in parts or "instrument rack" in " ".join(parts):
                category = "instrument"
            elif "audio effects" in " ".join(parts):
                category = "audio_fx"
            elif "midi effects" in " ".join(parts):
                category = "midi_fx"

            items.append({
                "name": name,
                "path": str(path),
                "extension": ext,
                "type": exts[ext],
                "category": category,
                "source": "Core Library" if "Core Library" in str(path) else "User/Packs"
            })

out = {
    "total_items": len(items),
    "items": items
}

output_path = Path.home() / "Library/Application Support/ArrangementGPS/ableton_library_index.json"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    json.dumps(out, indent=2, ensure_ascii=False)
)

print(f"Indexed {len(items)} items")
print(f"Saved: {output_path}")
