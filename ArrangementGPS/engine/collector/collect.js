import fs from "fs";
import path from "path";

const statusPath = path.resolve("engine/output/status.json");

if (!fs.existsSync(statusPath)) {
  console.error("Missing status.json. Run engine first.");
  process.exit(1);
}

const status = JSON.parse(fs.readFileSync(statusPath, "utf8"));

let completed = 0;
let waiting = 0;
let failed = 0;

const jobs = status.jobs.map((job) => {
  const manifestPath = path.resolve(job.expected_output.manifest);

  if (!fs.existsSync(manifestPath)) {
    waiting++;
    return { ...job, status: "waiting", manifest_found: false };
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const nextStatus = manifest.status || "waiting";

  if (nextStatus === "complete" || nextStatus === "completed") completed++;
  else if (nextStatus === "failed") failed++;
  else waiting++;

  return {
    ...job,
    status: nextStatus,
    manifest_found: true,
    output_path: manifest.output_path || job.expected_output.path
  };
});

const next = {
  ...status,
  updated_at: new Date().toISOString(),
  project_status: failed > 0 ? "needs_attention" : completed === jobs.length ? "ready_for_ableton" : "in_progress",
  totals: {
    waiting: waiting,
    running: 0,
    completed,
    failed
  },
  jobs
};

fs.writeFileSync(statusPath, JSON.stringify(next, null, 2));

console.log("Collector updated status.json");
console.log(next.totals);
console.log("Project status:", next.project_status);
