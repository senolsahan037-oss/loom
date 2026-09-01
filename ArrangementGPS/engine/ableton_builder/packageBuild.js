import fs from "fs";
import path from "path";

const files = [
  "engine/output/normalized_blueprint.json",
  "engine/output/status.json",
  "engine/output/ableton_build_plan.json",
  "engine/output/ableton_session_plan.json",
  "engine/output/ableton_build_manifest.json",
  "engine/output/ableton_dry_run_report.md"
];

const packageDir = path.resolve("engine/output/ableton_package");
fs.mkdirSync(packageDir, { recursive: true });

for (const file of files) {
  if (fs.existsSync(file)) {
    fs.copyFileSync(file, path.join(packageDir, path.basename(file)));
  }
}

const manifest = {
  created_at: new Date().toISOString(),
  package_type: "ableton_builder_input",
  files: files.filter(fs.existsSync).map((file) => path.basename(file)),
  status: "ready_for_builder"
};

fs.writeFileSync(
  path.join(packageDir, "package_manifest.json"),
  JSON.stringify(manifest, null, 2)
);

console.log("Ableton package created.");
console.log(packageDir);
