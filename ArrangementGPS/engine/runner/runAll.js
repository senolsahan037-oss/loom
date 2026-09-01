import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";

const statusPath = path.resolve("engine/output/status.json");

if (!fs.existsSync(statusPath)) {
  console.error("Missing status.json");
  process.exit(1);
}

while (true) {
  const status = JSON.parse(fs.readFileSync(statusPath, "utf8"));
  const next = status.jobs.find(j => j.status === "waiting" || j.status === "queued");

  if (!next) {
    console.log("All jobs completed.");
    break;
  }

  console.log(`Running: ${next.job_id}`);

  spawnSync("node", ["engine/runner/runNext.js"], {
    stdio: "inherit"
  });

  spawnSync("node", ["engine/collector/collect.js"], {
    stdio: "inherit"
  });
}
