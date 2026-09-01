import fs from "fs";
import path from "path";
import { generateCreativeBrief } from "../providers/openaiProvider.js";

const prompt = process.argv.slice(2).join(" ").trim();

if (!prompt) {
  console.error('Usage: node engine/training/generateOne.js "prompt"');
  process.exit(1);
}

const brief = await generateCreativeBrief(prompt);

const example = {
  id: "west_coast_dre_001",
  created_at: new Date().toISOString(),
  prompt,
  genre: "west_coast_hiphop",
  source_model: "openai",
  creative_brief: brief
};

const out = path.resolve("engine/training/examples/west_coast_dre_001.json");
fs.writeFileSync(out, JSON.stringify(example, null, 2));

console.log("Training example saved:");
console.log(out);
