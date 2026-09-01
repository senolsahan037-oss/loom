import "./TaskDispatch.css";

function formatTargetTrack(trackId, tracks) {
  return tracks.find((track) => track.id === trackId)?.name ?? trackId;
}

function formatSections(sectionIds, sections) {
  return sectionIds
    .map((sectionId) => sections.find((section) => section.id === sectionId)?.name ?? sectionId)
    .join(", ");
}

const jobSummaries = {
  "Drum Generator": "Shape the rhythm foundation and section lift",
  "Bass Generator": "Create low-end support and hook variations",
  "Chord Generator": "Build harmonic support across key sections",
  "Sample Generator": "Design the melodic texture and hook identity",
  "FX Generator": "Plan transitions, atmosphere, and releases",
  "Vocal Planner": "Map vocal space and hook focus",
};

function TaskDispatch({ blueprint }) {
  const { generator_jobs: jobs, tracks } = blueprint;
  const sections = blueprint.arrangement.sections;

  return (
    <section className="panel task-dispatch" aria-labelledby="tasks-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Production Jobs</p>
          <h2 id="tasks-title">Generator Queue</h2>
        </div>
      </div>

      <div className="task-dispatch__list">
        {jobs.map((job) => (
          <article className="agent-card" key={job.id}>
            <div className="agent-card__topline">
              <h3>{job.generator}</h3>
              <span className="status-pill">{job.status}</span>
            </div>
            <dl className="agent-card__meta">
              <div>
                <dt>Job</dt>
                <dd>{jobSummaries[job.generator] ?? "Prepare production part"}</dd>
              </div>
              <div>
                <dt>Target</dt>
                <dd>{formatTargetTrack(job.target_track, tracks)}</dd>
              </div>
              <div>
                <dt>Sections</dt>
                <dd>{formatSections(job.target_sections, sections)}</dd>
              </div>
              <div>
                <dt>Bars</dt>
                <dd>{job.payload.bar_range}</dd>
              </div>
              <div>
                <dt>Priority</dt>
                <dd>{job.priority}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{job.status}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

export default TaskDispatch;
