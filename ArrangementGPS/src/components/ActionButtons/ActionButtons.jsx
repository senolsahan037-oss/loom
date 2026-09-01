import "./ActionButtons.css";

function ActionButtons({ onExport }) {
  return (
    <section className="panel action-buttons" aria-label="Blueprint actions">
      <button className="button button--secondary" type="button" onClick={onExport}>
        Export Blueprint JSON
      </button>
      <button className="button" type="button" disabled>
        <span>Open in Ableton</span>
        <small>Coming Soon</small>
      </button>
      <button className="button" type="button" disabled>
        <span>Run Full Pipeline</span>
        <small>Coming Soon</small>
      </button>
      <button className="button" type="button" disabled>
        <span>Generate Drums</span>
        <small>Coming Soon</small>
      </button>
      <button className="button" type="button" disabled>
        <span>Generate Bass</span>
        <small>Coming Soon</small>
      </button>
    </section>
  );
}

export default ActionButtons;
