import { spawnSync } from "child_process";

const prompts = [
  "Dr. Dre tarzı West Coast G-Funk beat. 95 BPM.",
  "Dusty Boom Bap. 88 BPM.",
  "Dark Trip Hop. 82 BPM.",
  "Melancholic Arabesk Rap. 90 BPM.",
  "Aggressive Trap. 140 BPM.",
  "Memphis Rap. 138 BPM.",
  "Phonk. 135 BPM.",
  "Lo-Fi Hip Hop. 78 BPM.",
  "UK Drill. 142 BPM.",
  "Cinematic Hip Hop. 84 BPM."
];

for (const prompt of prompts) {
  console.log(`Generating: ${prompt}`);

  spawnSync(
    "node",
    [
      "SubverseDataset/api_generated/arrangementgps/generate_one.js",
      prompt
    ],
    {
      stdio: "inherit"
    }
  );
}

console.log("Batch generation finished.");
