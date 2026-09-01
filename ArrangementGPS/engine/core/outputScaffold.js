import fs from "fs";
import path from "path";

export function createOutputScaffold(agentDispatch) {
  for (const job of agentDispatch) {
    const outputPath = path.resolve(job.expected_output.path);
    const manifestPath = path.resolve(job.expected_output.manifest);

    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.mkdirSync(path.dirname(manifestPath), { recursive: true });

    if (!fs.existsSync(manifestPath)) {
      fs.writeFileSync(
        manifestPath,
        JSON.stringify(
          {
            job_id: job.job_id,
            agent: job.agent,
            target_track: job.target_track,
            status: "waiting",
            expected_output_type: job.expected_output.type,
            output_path: job.expected_output.path,
            created_by: "ArrangementGPS"
          },
          null,
          2
        )
      );
    }
  }
}
