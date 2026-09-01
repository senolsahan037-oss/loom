import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";

const senseiDir = path.resolve(process.env.HOME, "Desktop/Loom/Sensei");
const outDir = path.resolve("engine/output/agent_outputs/drums");
const outMidi = path.join(outDir, "full_drums.mid");
const manifestPath = path.join(outDir, "full_drums.manifest.json");

fs.mkdirSync(outDir, { recursive: true });

const prompt = process.argv.slice(2).join(" ").trim() || "boom bap dusty swing punchy drums";

const result = spawnSync("python3", [
  "cli/export_drum_clip.py",
  "--prompt",
  prompt,
  "--out-dir",
  outDir
], {
  cwd: senseiDir,
  encoding: "utf8"
});

if (result.status !== 0) {
  console.error(result.stderr || result.stdout);
  process.exit(result.status || 1);
}

const payload = JSON.parse(result.stdout.trim());

console.log("Sensei MIDI generated:");
console.log(payload.output);
