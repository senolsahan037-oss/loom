import subprocess
import sys
from pathlib import Path
from datetime import datetime

als = sys.argv[1]

base = Path("~/Desktop/Loom/AIMixMaster").expanduser()
out_dir = base / "reports"
out_dir.mkdir(parents=True, exist_ok=True)

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
report_dir = out_dir / f"als_report_{stamp}"
report_dir.mkdir()

jobs = [
    ("01_timeline.txt", ["python3", str(base / "als_timeline_ascii.py"), als, "120", "540"]),
    ("02_mixer_compact.txt", ["python3", str(base / "als_mixer_compact.py"), als]),
    ("03_sources_summary.txt", ["python3", str(base / "als_clip_sources.py"), als]),
    ("04_audio_clips_summary.txt", ["python3", str(base / "als_audio_clip_detail.py"), als, "--all"]),
    ("05_midi_clips.txt", ["python3", str(base / "als_midi_inspector.py"), als]),
    ("06_midi_devices.txt", ["python3", str(base / "als_midi_devices.py"), als]),
    ("07_routing.txt", ["python3", str(base / "als_routing_inspector.py"), als]),
    ("08_automation.txt", ["python3", str(base / "als_automation_inspector.py"), als]),
]

for filename, cmd in jobs:
    path = report_dir / filename
    result = subprocess.run(cmd, capture_output=True, text=True)
    content = result.stdout

    if result.stderr:
        content += "\n\n--- STDERR ---\n" + result.stderr

    path.write_text(content)
    print("OK:", filename)

print("\nReport folder:")
print(report_dir)
