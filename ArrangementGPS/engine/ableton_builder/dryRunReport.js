import fs from "fs";
import path from "path";

const manifestPath = path.resolve("engine/output/ableton_build_manifest.json");
const outPath = path.resolve("engine/output/ableton_dry_run_report.md");

if (!fs.existsSync(manifestPath)) {
  console.error("Missing ableton_build_manifest.json");
  process.exit(1);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const actions = manifest.actions ?? [];

const groups = actions.filter(a => a.type === "create_group_track");
const tracks = actions.filter(a => a.type === "create_midi_track");
const locators = actions.filter(a => a.type === "create_locator");

let md = `# Ableton Builder Dry Run Report

Project: ${manifest.project?.name ?? "Untitled"}
BPM: ${manifest.project?.bpm ?? "Unknown"}
Key: ${manifest.project?.key ?? "Unknown"}

Status: ${manifest.status}

## Summary

- Groups: ${groups.length}
- MIDI Tracks: ${tracks.length}
- Locators: ${locators.length}

## Groups

${groups.map(g => `- ${g.name}`).join("\n")}

## Tracks

${tracks.map(t => `- ${t.writes_to} ← ${t.source} | Bars ${t.clip_start_bar}-${t.clip_end_bar}`).join("\n")}

## Locators

${locators.map(l => `- ${l.name}: Bar ${l.start_bar}-${l.end_bar} (${l.start_time} → ${l.end_time})`).join("\n")}

## Render Policy

\`\`\`json
${JSON.stringify(manifest.render_policy ?? {}, null, 2)}
\`\`\`
`;

fs.writeFileSync(outPath, md);

console.log("Dry run report created.");
console.log(`Saved: ${outPath}`);
