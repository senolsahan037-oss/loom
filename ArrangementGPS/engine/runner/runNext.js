import fs from "fs";
import path from "path";

const statusPath = path.resolve("engine/output/status.json");
const blueprintPath = path.resolve("engine/output/normalized_blueprint.json");

if (!fs.existsSync(statusPath)) {
  console.error("Missing status.json. Run engine first.");
  process.exit(1);
}

const status = JSON.parse(fs.readFileSync(statusPath, "utf8"));
const blueprint = fs.existsSync(blueprintPath)
  ? JSON.parse(fs.readFileSync(blueprintPath, "utf8"))
  : {};

const job = status.jobs.find((j) => j.status === "waiting");

if (!job) {
  console.log("No waiting job found.");
  process.exit(0);
}

const outputPath = path.resolve(job.expected_output.path);
const manifestPath = path.resolve(job.expected_output.manifest);

fs.mkdirSync(path.dirname(outputPath), { recursive: true });

fs.writeFileSync(outputPath, JSON.stringify({
  job_id: job.job_id,
  agent: job.agent,
  target_track: job.target_track,
  output_type: job.expected_output.type,
  source_blueprint: "engine/output/normalized_blueprint.json",
  placeholder: true,
  context: { project: blueprint.project }
}, null, 2));

fs.writeFileSync(manifestPath, JSON.stringify({
  job_id: job.job_id,
  agent: job.agent,
  target_track: job.target_track,
  status: "complete",
  expected_output_type: job.expected_output.type,
  output_path: job.expected_output.path,
  completed_at: new Date().toISOString(),
  created_by: "ArrangementGPS Test Job Runner"
}, null, 2));

console.log(`Completed test job: ${job.job_id}`);
