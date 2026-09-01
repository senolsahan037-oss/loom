import express from "express";
import generateBlueprintRouter from "./routes/generateBlueprint.js";
import { getOllamaReachability, OLLAMA_MODEL } from "./services/ollamaClient.js";

const PORT = Number(process.env.PORT ?? 3001);
const HOST = process.env.HOST ?? "127.0.0.1";
const app = express();
const allowedOrigins = new Set([
  "http://localhost:5173",
  "http://127.0.0.1:5173",
  "http://0.0.0.0:5173",
]);

app.use((request, response, next) => {
  const origin = request.headers.origin;

  response.setHeader(
    "Access-Control-Allow-Origin",
    allowedOrigins.has(origin) ? origin : "http://localhost:5173"
  );
  response.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (request.method === "OPTIONS") {
    response.status(204).end();
    return;
  }

  next();
});

app.use(express.json({ limit: "1mb" }));

app.get("/api/health", async (request, response) => {
  response.status(200).json({
    status: "ok",
    service: "ArrangementGPS Backend",
    ollama: await getOllamaReachability(),
    model: OLLAMA_MODEL,
  });
});

app.get("/api/version", (request, response) => {
  response.status(200).json({ backend: "1.0.0" });
});

app.use("/api/generate-blueprint", generateBlueprintRouter);

const server = app.listen(PORT, HOST, () => {
  console.log(`ArrangementGPS backend listening on http://${HOST}:${PORT}`);
});

export default server;
