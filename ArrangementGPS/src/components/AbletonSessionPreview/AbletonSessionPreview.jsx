import "./AbletonSessionPreview.css";

const trackIcons = {
  Drums: "DR",
  Bass: "BA",
  Chords: "CH",
  Melody: "ML",
  Vocal: "VO",
  FX: "FX",
};

function AbletonSessionPreview({ blueprint }) {
  const { tracks } = blueprint;

  return (
    <section className="panel ableton-preview" aria-labelledby="ableton-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Session Plan</p>
          <h2 id="ableton-title">Ableton Session Preview</h2>
        </div>
      </div>

      <div className="ableton-preview__tracks">
        {tracks.map((track) => (
          <article className="ableton-track" key={track.id}>
            <div className="ableton-track__name">
              <span aria-hidden="true">{trackIcons[track.name] ?? "TR"}</span>
              <strong>{track.name}</strong>
            </div>
            <div className="ableton-track__lanes" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <div className="ableton-track__status">
              <span>Role</span>
              <strong>{track.role}</strong>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export default AbletonSessionPreview;
