import "./Header.css";

const pipelineSteps = ["Prompt", "Blueprint", "Ableton", "Drums", "Bass", "FX", "Export"];

function Header() {
  return (
    <header className="app-header">
      <div className="app-header__content">
        <p className="app-header__eyebrow">AI music production control center</p>
        <h1>ArrangementGPS</h1>
        <p className="app-header__subtitle">
          Transform natural language prompts into complete Ableton project blueprints.
        </p>
      </div>

      <div className="app-header__panel">
        <div className="app-header__signal" aria-label="Prototype status">
          <span />
          Live Demo
        </div>
        <ol className="pipeline" aria-label="Pipeline progress">
          {pipelineSteps.map((step, index) => (
            <li className="pipeline__item" key={step}>
              {index > 0 ? (
                <span className="pipeline__arrow" aria-hidden="true">
                  ↓
                </span>
              ) : null}
              <span
                className={step === "Blueprint" ? "pipeline__step is-active" : "pipeline__step"}
              >
                {step}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </header>
  );
}

export default Header;
