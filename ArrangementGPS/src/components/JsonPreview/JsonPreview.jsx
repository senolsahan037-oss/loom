import { useState } from "react";
import "./JsonPreview.css";

function JsonPreview({ data }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <section className="panel json-preview" aria-labelledby="json-title">
      <button
        className="json-preview__toggle"
        type="button"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <div>
          <p className="panel__kicker">Data</p>
          <h2 id="json-title">Developer Mode</h2>
        </div>
        <span>{isOpen ? "Hide JSON" : "Show JSON"}</span>
      </button>

      {isOpen ? (
        <pre className="json-preview__code">
          <code>{JSON.stringify(data, null, 2)}</code>
        </pre>
      ) : (
        <p className="json-preview__summary">
          Inspect the full blueprint JSON used for export.
        </p>
      )}
    </section>
  );
}

export default JsonPreview;
