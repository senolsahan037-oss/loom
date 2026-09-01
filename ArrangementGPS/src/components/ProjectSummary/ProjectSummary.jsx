import "./ProjectSummary.css";

const summaryFields = [
  ["Project Name", "name", "AG"],
  ["BPM", "bpm", "BPM"],
  ["Key", "key", "KEY"],
  ["Groove", "groove_profile", "GRV"],
  ["Mood", "mood", "MOD"],
  ["Total Bars", "total_bars", "BAR"],
];

function ProjectSummary({ blueprint }) {
  const project = blueprint.project;

  return (
    <section className="panel project-summary" aria-labelledby="summary-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Blueprint</p>
          <h2 id="summary-title">Project Summary</h2>
        </div>
      </div>

      <dl className="project-summary__grid">
        {summaryFields.map(([label, key, icon]) => (
          <div className="project-summary__item" key={key}>
            <div className="project-summary__icon" aria-hidden="true">
              {icon}
            </div>
            <div>
              <dt>{label}</dt>
              <dd>{project[key]}</dd>
            </div>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default ProjectSummary;
