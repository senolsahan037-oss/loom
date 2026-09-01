const OUTPUT_ROOT = "engine/output/agent_outputs";

export function buildAgentDispatch(productionTree) {
  const jobs = [];

  for (const group of productionTree.tracks) {
    for (const child of group.children ?? []) {
      const safeId = child.id.replaceAll(".", "_");

      jobs.push({
        job_id: `job_${safeId}`,
        agent: child.generator,
        target_track: child.id,
        target_group: group.id,
        priority: child.priority,
        locked: child.locked ?? false,
        input_refs: {
          production_node: `production_tree.${child.id}`,
          sound_intent: `sound_intent.tracks.${child.id}`,
          scene_lane: `scene.lanes.${group.id}`
        },
        writes_to: `Ableton.Track.${group.name}.${child.name}`,
        depends_on: child.generator === "ai_sound_designer" ? [`job_${safeId.replace("fx_", "")}`] : [],
        expected_output: {
          type: child.generator === "ai_sound_designer" ? "sound_design_plan" : "midi_clip",
          path: `${OUTPUT_ROOT}/${group.id}/${safeId}.json`,
          manifest: `${OUTPUT_ROOT}/${group.id}/${safeId}.manifest.json`
        },
        failure_policy: {
          on_fail: "continue_project",
          fallback_output: "empty_clip",
          report_error_to_status: true
        },
        status: "queued"
      });
    }
  }

  return jobs.sort((a, b) => a.priority - b.priority);
}
