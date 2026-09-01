import fs from "fs";
import path from "path";
import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const prompt = process.argv.slice(2).join(" ").trim();

if (!prompt) {
  console.error('Usage: node SubverseDataset/api_generated/arrangementgps/generate_one.js "prompt"');
  process.exit(1);
}

const response = await client.responses.create({
  model: "gpt-4.1-mini",
  input: `
You are creating a SubverseDataset training example for ArrangementGPS.

Return ONLY raw valid JSON.
No markdown.
No code fences.
No explanation.

Create a shared production dataset record for this prompt:
${prompt}

Do NOT write MIDI notes, lyrics, chord progressions, basslines, melodies, or drum patterns.

Use this exact top-level structure:

{
  "project": {
    "name": "",
    "genre": "",
    "subgenre": "",
    "bpm": 0,
    "key": "",
    "mood": "",
    "total_bars": 80
  },
  "arrangement": {
    "energy_curve": [],
    "notes": ""
  },
  "scene": {
    "sections": [
      {
        "id": "intro",
        "name": "Intro",
        "start_bar": 1,
        "end_bar": 8,
        "energy": 20
      }
    ],
    "lanes": {
      "drums": { "activity": {} },
      "bass": { "activity": {} },
      "chords": { "activity": {} },
      "melody": { "activity": {} },
      "vocal": { "activity": {} },
      "fx": { "activity": {} }
    },
    "event_markers": []
  },
  "production_tree": {},
  "sound_intent": {},
  "routing_intent": {},
  "track_roles": {
    "drums": "",
    "bass": "",
    "chords": "",
    "melody": "",
    "vocal": "",
    "fx": ""
  },
  "silence_policy": {
    "allow_pre_drop_space": true,
    "allow_hook_dropout": true,
    "preferred_empty_bars": []
  },
  "priority": {
    "drums": 0,
    "bass": 0,
    "chords": 0,
    "melody": 0,
    "vocal": 0,
    "fx": 0
  },
  "designer_intent": {},
  "presetor_intent": {},
  "agents": {
    "sensei_drum": { "brief": "" },
    "bass_generator": { "brief": "" },
    "harmony_generator": { "brief": "" },
    "melody_generator": { "brief": "" },
    "ai_designer": { "brief": "" },
    "presetor": { "brief": "" },
    "songgps": { "brief": "" },
    "ai_mixmaster": { "brief": "" }
  },
  "render_policy": {
    "presetor_render_behavior": "bypass_temporary_arrangement_chains",
    "songgps_render_target": "clean_audio",
    "mixmaster_receives": "unprocessed_channel_audio"
  },
  "confidence": {
    "arrangement": 0.0,
    "sound_intent": 0.0,
    "agent_dispatch": 0.0
  },
  "metadata": {
    "source": "openai",
    "dataset_type": "arrangementgps_training_example"
  }
}

Rules:
- Section ids must be: intro, verse_1, hook, verse_2, bridge, final_hook, outro.
- Lane activity values must be 0-100.
- Event markers must include: track, bar, type, label, reason.
- Agent briefs should describe what each agent needs to do, not actual musical notes.
`
});

let text = response.output_text.trim();
text = text.replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();

const parsed = JSON.parse(text);

const outDir = "SubverseDataset/api_generated/arrangementgps";
const slug = prompt
  .toLowerCase()
  .normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "")
  .replace(/[^a-z0-9]+/g, "_")
  .replace(/^_+|_+$/g, "")
  .slice(0, 60);

const id = `arrangementgps_${Date.now()}_${slug}`;
const outPath = path.join(outDir, `${id}.json`);

fs.writeFileSync(outPath, JSON.stringify(parsed, null, 2));

console.log(`Saved: ${outPath}`);
