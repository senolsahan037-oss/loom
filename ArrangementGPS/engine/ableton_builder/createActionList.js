import fs from "fs";
import path from "path";

// The package directory name is derived from the project name the same way
// createProjectPackage.js derived it -- reading it back from the pre-package
// session plan keeps the two in sync without a shared module for two lines.
const OUTPUT_DIR = path.resolve(process.env.ARRANGEMENTGPS_OUTPUT_DIR || "engine/output");
const sourceSessionPlan = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, "ableton_session_plan.json"), "utf8"));
// The package stage records where it wrote; this stage reads that instead of
// deriving the directory name a second time.
const packageLocation = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, "package_location.json"), "utf8"));
const safeName = packageLocation.safe_name;
const packageDir = packageLocation.build_dir;
const manifestPath = path.join(packageDir, "package_manifest.json");
const sessionPath = path.join(packageDir, "ableton_session_plan.json");
const outPath = path.join(packageDir, "ableton_action_list.json");

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const session = JSON.parse(fs.readFileSync(sessionPath, "utf8"));

const actions = [];

for (const group of session.groups ?? []) {
  actions.push({
    action: "create_group_track",
    group_id: group.id,
    name: group.name,
    priority: group.priority
  });
}

for (const locator of session.locators ?? []) {
  actions.push({
    action: "create_locator",
    id: locator.id,
    name: locator.name,
    start_bar: locator.start_bar,
    end_bar: locator.end_bar,
    start_time: locator.start_time,
    end_time: locator.end_time,
    energy: locator.energy
  });
}

for (const track of manifest.tracks ?? []) {
  const trackData = JSON.parse(
    fs.readFileSync(path.join(packageDir, track.file), "utf8")
  );

  actions.push({
    action: "create_midi_track",
    track_id: trackData.track_id,
    group: trackData.group,
    name: trackData.ableton_name,
    writes_to: trackData.writes_to,
    clip_start_bar: trackData.clip_start_bar,
    clip_end_bar: trackData.clip_end_bar,
    mute_regions: trackData.mute_regions,
    section_activity: trackData.section_activity ?? {},
    source: trackData.source,
    output_type: trackData.output_type,
    instrument_family: trackData.instrument_family ?? null,
    sensei_role: trackData.sensei_role ?? null,
    device_chain: trackData.device_chain
  });
}

fs.writeFileSync(outPath, JSON.stringify({
  created_at: new Date().toISOString(),
  project: manifest.project,
  action_count: actions.length,
  actions
}, null, 2));

console.log("Ableton action list created:");
console.log(outPath);
console.log(`Actions: ${actions.length}`);
