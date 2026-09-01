import os
from core.agent.providers.base import LLMProviderBase

class VertexLLMProvider(LLMProviderBase):
    """Vertex AI provider wrapper that delegates to tools.sensei.ask_vertex to support mock patching in tests."""
    def __init__(self):
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "project-62238635-aae4-41f4-880")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model_name = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
        
    def ask(self, prompt: str, system_instruction: str = None) -> str:
        try:
            from tools.sensei import ask_vertex
            return ask_vertex(prompt, self.model_name)
        except ImportError:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project=self.project_id, location=self.location)
            if system_instruction:
                model = GenerativeModel(self.model_name, system_instruction=system_instruction)
            else:
                model = GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            return response.text or ""
