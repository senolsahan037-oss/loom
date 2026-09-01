import fs from "fs";
import path from "path";
import { buildScene } from "./core/sceneBuilder.js";
import { buildProductionTree } from "./core/productionTree.js";
import { buildSoundIntent } from "./core/soundIntent.js";
import { buildAgentDispatch } from "./core/agentDispatch.js";
import { createOutputScaffold } from "./core/outputScaffold.js";
import { createStatusFile } from "./core/statusManager.js";
import { deriveMoodFromPrompt } from "./core/promptMood.js";
import { generateCreativeBrief } from "./providers/openaiProvider.js";

const prompt = process.argv.slice(2).join(" ").trim();

if (!prompt) {
  console.error("Usage: node engine/run.js \"your music prompt\"");
  process.exit(1);
}

const creative_brief = await generateCreativeBrief(prompt);
const { mode, genre, key, bpm } = deriveMoodFromPrompt(prompt);

// Only used when the prompt does not state one. They are defaults, not
// decisions -- a prompt that says "126 bpm" or "in F minor" overrides them.
const DEFAULT_BPM = 95;
const DEFAULT_ROOT = "D";

// Each run gets its own project name (prompt + timestamp) so
// createProjectPackage.js writes to a fresh Builds/<name> directory instead
// of always overwriting the same "Local Engine Test" project -- lets
// different prompts (different mode/genre pools) be built and inspected
// side by side rather than one fixed project being reused for every test.
const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "").replace("T", "-");
const projectName = `${prompt.slice(0, 60)} ${timestamp}`;

const outputDir = path.resolve("engine/output");
fs.mkdirSync(outputDir, { recursive: true });

const scene = buildScene();
const production_tree = buildProductionTree();
const sound_intent = buildSoundIntent();
const agent_dispatch = buildAgentDispatch(production_tree);
createOutputScaffold(agent_dispatch);
const agent_status = createStatusFile(agent_dispatch);

const blueprint = {
  creative_brief,
  project: {
    name: projectName,
    user_prompt: prompt,
    bpm: bpm ?? DEFAULT_BPM,
    key: `${key?.root ?? DEFAULT_ROOT} ${mode === "major" ? "Major" : "Minor"}`,
    // So a later reader can tell a stated value from a fallback without
    // re-parsing the prompt.
    bpm_source: bpm ? "prompt" : "default",
    key_source: key ? "prompt" : "default",
    genre,
    total_bars: 80
  },
  scene,
  production_tree,
  sound_intent,
  render_policy: {
    arrangement_chain_mode: "temporary",
    presetor_chains_are_committed: false,
    presetor_render_behavior: "bypass_temporary_arrangement_chains",
    remove_presetor_devices_before_songgps_render: false,
    songgps_render_target: "clean_audio",
    mixmaster_receives: "unprocessed_channel_audio",
    failure_policy: "continue_project_with_empty_channels",
    failed_agent_result: "empty_midi_or_empty_audio_region",
    reason: "Arrangement chains are for production monitoring only. Presetor bypasses temporary chains before SongGPS clean render. If an agent fails, the Ableton project still builds and the failed channel remains empty."
  },
  agent_dispatch,
  agent_status,
  status: "local_scene_builder_ready"
};

fs.writeFileSync(
  path.join(outputDir, "normalized_blueprint.json"),
  JSON.stringify(blueprint, null, 2)
);

console.log("ArrangementGPS local scene builder ready.");
console.log("Saved: engine/output/normalized_blueprint.json");
