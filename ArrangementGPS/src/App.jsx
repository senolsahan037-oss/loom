import { useMemo, useState } from "react";
import "./App.css";
import { exportJson } from "./utils/exportJson";
import { generateBlueprintFromPrompt, initialBlueprint } from "./services/blueprintGenerator";
import Header from "./components/Header/Header";
import PromptPanel from "./components/PromptPanel/PromptPanel";
import ProjectSummary from "./components/ProjectSummary/ProjectSummary";
import ArrangementTimeline from "./components/ArrangementTimeline/ArrangementTimeline";
import TaskDispatch from "./components/TaskDispatch/TaskDispatch";
import JsonPreview from "./components/JsonPreview/JsonPreview";
import ActionButtons from "./components/ActionButtons/ActionButtons";
import AbletonSessionPreview from "./components/AbletonSessionPreview/AbletonSessionPreview";
import ActivityLog from "./components/ActivityLog/ActivityLog";

function App() {
  const [prompt, setPrompt] = useState(
    "Dark boom bap with arabesk textures, 95 BPM, emotional hook, BabaSultan vocal space."
  );
  const [blueprint, setBlueprint] = useState(initialBlueprint);
  const [generationError, setGenerationError] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);

  const jsonFileName = useMemo(
    () =>
      `${blueprint.project.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-blueprint.json`,
    [blueprint.project.name]
  );

  async function handleGenerate() {
    setGenerationError("");
    setIsGenerating(true);

    try {
      const nextBlueprint = await generateBlueprintFromPrompt(prompt);
      setBlueprint(nextBlueprint);
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "Could not generate a valid blueprint.");
    } finally {
      setIsGenerating(false);
    }
  }

  function handleExport() {
    exportJson(blueprint, jsonFileName);
  }

  return (
    <main className="app-shell">
      <Header />

      <section className="control-grid" aria-label="ArrangementGPS control center">
        <div className="control-grid__main">
          <div className="content-top-row">
            <PromptPanel
              prompt={prompt}
              onPromptChange={setPrompt}
              onGenerate={handleGenerate}
              isGenerating={isGenerating}
              error={generationError}
            />
            <ProjectSummary blueprint={blueprint} />
          </div>
        </div>

        <aside className="control-grid__side" aria-label="Production status">
          <AbletonSessionPreview blueprint={blueprint} />
          <ActivityLog entries={blueprint.production_log} />
        </aside>
      </section>

      <ArrangementTimeline blueprint={blueprint} />

      <section className="production-grid" aria-label="AI production pipeline">
        <TaskDispatch blueprint={blueprint} />
        <JsonPreview data={blueprint} />
      </section>

      <ActionButtons onExport={handleExport} />
    </main>
  );
}

export default App;
