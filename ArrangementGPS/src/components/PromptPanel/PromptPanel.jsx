import "./PromptPanel.css";

function PromptPanel({ prompt, onPromptChange, onGenerate, isGenerating, error }) {
  return (
    <section className="panel prompt-panel" aria-labelledby="prompt-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Input</p>
          <h2 id="prompt-title">Arrangement Prompt</h2>
        </div>
      </div>

      <textarea
        className="prompt-panel__textarea"
        value={prompt}
        onChange={(event) => onPromptChange(event.target.value)}
        placeholder="Dark boom bap with arabesk textures, 95 BPM, emotional hook, BabaSultan vocal space."
      />

      <div className="prompt-panel__footer">
        <button className="button button--primary" type="button" onClick={onGenerate} disabled={isGenerating}>
          {isGenerating ? "Generating..." : "Generate Arrangement"}
        </button>
        <p>This generates a production blueprint, not audio.</p>
      </div>

      {error ? <p className="prompt-panel__error">{error}</p> : null}
    </section>
  );
}

export default PromptPanel;
