import "./ActivityLog.css";

function ActivityLog({ entries }) {
  return (
    <section className="panel activity-log" aria-labelledby="activity-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Console</p>
          <h2 id="activity-title">Production Log</h2>
        </div>
      </div>

      <div className="activity-log__console" role="log" aria-live="polite">
        {entries.map((entry) => (
          <p className={`activity-log__entry is-${entry.type}`} key={entry.message}>
            <span aria-hidden="true">{entry.type === "complete" ? "✓" : "⏳"}</span>
            {entry.message}
          </p>
        ))}
      </div>
    </section>
  );
}

export default ActivityLog;
