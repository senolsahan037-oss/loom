import fs from "fs";
import path from "path";

export function createStatusFile(agentDispatch) {
  const statusPath = path.resolve("engine/output/status.json");

  const status = {
    created_at: new Date().toISOString(),
    project_status: "waiting",
    totals: {
      queued: agentDispatch.length,
      running: 0,
      completed: 0,
      failed: 0
    },
    jobs: agentDispatch.map((job) => ({
      job_id: job.job_id,
      agent: job.agent,
      target_track: job.target_track,
      target_group: job.target_group,
      status: "waiting",
      expected_output: job.expected_output,
      writes_to: job.writes_to ?? null,
      depends_on: job.depends_on ?? []
    }))
  };

  fs.writeFileSync(statusPath, JSON.stringify(status, null, 2));
  return status;
}
