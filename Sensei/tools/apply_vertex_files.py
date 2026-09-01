import json
import sys
from pathlib import Path

ALLOWED_PREFIXES = [
    "tools/sensei.py",
    "core/agent/",
    "tests/test_sensei_agent_",
]

def allowed(path):
    return any(path == p or path.startswith(p) for p in ALLOWED_PREFIXES)

def main():
    if len(sys.argv) != 2:
        print("usage: python3 tools/apply_vertex_files.py files.json")
        sys.exit(1)

    data = json.loads(Path(sys.argv[1]).read_text())
    files = data.get("files", [])

    for item in files:
        path = item["path"]
        content = item["content"]

        if not allowed(path):
            print(f"BLOCKED: {path}")
            continue

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        print(f"WROTE: {path}")

if __name__ == "__main__":
    main()
