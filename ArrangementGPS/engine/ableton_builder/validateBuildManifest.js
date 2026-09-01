import fs from "fs";
import path from "path";

const manifestPath = path.resolve("engine/output/ableton_build_manifest.json");

if (!fs.existsSync(manifestPath)) {
  console.error("Missing ableton_build_manifest.json");
  process.exit(1);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

const actions = manifest.actions ?? [];

const summary = {
  groups: actions.filter(a => a.type === "create_group_track").length,
  midi_tracks: actions.filter(a => a.type === "create_midi_track").length,
  locators: actions.filter(a => a.type === "create_locator").length
};

const problems = [];

for (const action of actions) {
  if (action.type === "create_midi_track") {
    if (!action.track_id) problems.push("Missing track_id");
    if (!action.name) problems.push(`Missing name for ${action.track_id}`);
    if (!action.writes_to) problems.push(`Missing writes_to for ${action.track_id}`);
    if (!action.source) problems.push(`Missing source for ${action.track_id}`);
  }

  if (action.type === "create_locator") {
    if (!action.name) problems.push("Missing locator name");
    if (!action.start_bar) problems.push(`Missing locator start_bar for ${action.name}`);
  }
}

manifest.validation = {
  valid: problems.length === 0,
  summary,
  problems
};

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

console.log("Build manifest validation complete.");
console.log(JSON.stringify(manifest.validation, null, 2));
