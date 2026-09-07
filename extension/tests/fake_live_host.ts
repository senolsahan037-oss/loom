// A real bridge consumer over a fake Live: the extension's own queue code
// (src/bridge.ts, unchanged) polling a directory, driven from another process
// through the file contract. mcp_server/tests/test_bridge_consumer_real.py
// starts this, talks to it through the MCP's own client, and kills it to
// simulate a host crash. This is the protocol proof; the Python fake in
// mcp_server/tests only stands in for it where speed matters.
//
//   tsx tests/fake_live_host.ts --root DIR --session ID [--hold-file PATH]
//        [--crash-after-mutation] [--snapshot PATH] [--request-ms N] [--state-ms N]
//
//   --hold-file             object-creating mutations wait while this file exists
//   --crash-after-mutation  exit hard right after the first mutation (before any outcome)
//   --snapshot              write the fake set's contents here after every mutation
import { writeFileSync } from "node:fs";
import { startBridge } from "../src/bridge.js";
import { FakeLive, faults } from "./fakes.js";

function arg(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}
const root = arg("--root");
const session = arg("--session");
if (!root || !session) {
  console.error("usage: fake_live_host.ts --root DIR --session ID [--hold-file PATH] [--crash-after-mutation] [--snapshot PATH]");
  process.exit(2);
}
const holdFile = arg("--hold-file") ?? null;
const snapshotPath = arg("--snapshot") ?? null;
const crashAfterMutation = process.argv.includes("--crash-after-mutation");

const live = new FakeLive();
live.tracks[0].drumPadNotes = [36, 38, 42, 46];
live.tracks[1].devices.push({ name: "Operator", className: "Operator", parameters: [] });

faults.holdFile = holdFile;
faults.onMutation = () => {
  if (snapshotPath) writeFileSync(snapshotPath, JSON.stringify(live.snapshot(), null, 1));
  if (crashAfterMutation) {
    // The host dies with the mutation applied and no outcome written: the
    // claim file and the journal's `started` line are all that remain.
    process.stdout.write("crashed after mutation\n");
    process.exit(137);
  }
};

const handle = startBridge(live, root, (line) => process.stdout.write(`${line}\n`), {
  sessionId: session,
  requestMs: Number(arg("--request-ms") ?? 20),
  stateMs: Number(arg("--state-ms") ?? 200),
});
process.stdout.write(`ready ${handle.sessionId}\n`);
// The bridge's own timers are unref'd; this one keeps the host alive.
const keepAlive = setInterval(() => {}, 1000);
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    handle.stop();
    clearInterval(keepAlive);
    process.exit(0);
  });
}
