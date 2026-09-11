# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
class LLMProviderBase:
    """Abstract base class representing an LLM text generation provider."""
    def ask(self, prompt: str, system_instruction: str = None) -> str:
        raise NotImplementedError()
