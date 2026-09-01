import fs from "fs";
import path from "path";

const root = "SubverseDataset/api_generated/arrangementgps";
const files = fs.readdirSync(root).filter(f => f.endsWith(".json"));

const required = [
  "project",
  "arrangement",
  "scene",
  "production_tree",
  "sound_intent",
  "agents",
  "render_policy",
  "metadata"
];

const report = {
  total_files: files.length,
  valid_json: 0,
  invalid_json: [],
  missing_fields: {}
};

for (const file of files) {
  const full = path.join(root, file);

  try {
    const data = JSON.parse(fs.readFileSync(full, "utf8"));
    report.valid_json++;

    for (const field of required) {
      if (!(field in data)) {
        report.missing_fields[file] ||= [];
        report.missing_fields[file].push(field);
      }
    }
  } catch (error) {
    report.invalid_json.push(file);
  }
}

const out = "SubverseDataset/dataset_audit_report.json";
fs.writeFileSync(out, JSON.stringify(report, null, 2));

console.log(JSON.stringify(report, null, 2));
console.log(`Saved: ${out}`);
