from typing import List, Dict, Any, Tuple
from core.agent.permissions import PermissionsManager
from core.agent.protocol_validation import validate_protocol
from core.agent.providers.vertex import VertexLLMProvider
from core.agent.context_node import select_context

class SenseiAgent:
    """Orchestrates agent operations: permissions check, context selection, LLM invocation, and output validation."""
    def __init__(self, mode: str, provider=None):
        self.mode = mode.lower()
        self.permissions = PermissionsManager(self.mode)
        self.provider = provider or VertexLLMProvider()

    def run(self, task: str, deep: bool = False, with_context: bool = True) -> str:
        # Check permissions
        if not self.permissions.is_read_allowed():
            raise PermissionError("Read permission denied.")

        # Get context if needed
        context_text = ""
        if with_context:
            _, context_text, _, _ = select_context(task, self.mode, deep=deep)

        # Build prompt
        prompt = task
        if context_text:
            prompt = prompt + "\n\n=== Context ===\n" + context_text

        # Ableton-only research policy is always in system instructions
        system_instruction = (
            "Sen Sensei agentsın. Kendi kullanacağın araçları ve DAW terminolojisini tanırsın. "
            "Müzikal sohbet edebilir, müzik terimlerini, türleri (genre) ve bağlamları anlayabilirsin. "
            "Kendi araçlarındaki eksiklikleri fark edip geliştirme önerileri sunabilirsin. KESİNLİKLE kod yazamazsın.\n\n"
            "İnternet araştırması yaparken yalnızca kamuya açık Ableton ile ilgili belgeleri (ableton.com, help.ableton.com, www.ableton.com) okuyabilirsin. "
            "Yalnızca şu konulara izin verilmiştir: Ableton Live/Max for Live belgeleri, Live API referansları, MIDI klipleri, Drum Rack, Groove Pool, Remote Scripts, Python kontrol yüzeyi betikleri ve kamuya açık Ableton dosya biçimleri. "
            "Genel web araştırması, rastgele bloglar, StackOverflow, kod üretimi, ilgisiz siteleri kazımak veya bağlı bir arama aracı olmadan web araştırması yaptığını iddia etmek kesinlikle yasaktır. "
            "Eğer bir arama/araştırma aracı mevcut değilse, kesinlikle şu cümleyi söylemelisin: \"Web research tool unavailable.\"\n\n"
            "You are Sensei agent. You know the tools you use and DAW terminology. "
            "You can engage in musical conversations, understand music terms, genres, and contexts. "
            "You can identify deficiencies in your own tools and present development proposals. You CANNOT write code.\n\n"
            "When doing web research, you may ONLY read public Ableton-related documentation from allowed domains (ableton.com, help.ableton.com, www.ableton.com). "
            "Allowed topics are: Ableton Live/Max for Live documentation, Live API references, MIDI clips, Drum Rack, Groove Pool, Remote Scripts, Python control surface scripts, and publicly documented Ableton file formats. "
            "Forbidden: general web research, random blogs, StackOverflow, code generation, scraping unrelated sites, and claiming web research without using a connected research/search tool. "
            "If a research/search tool is unavailable, you MUST say: \"Web research tool unavailable.\""
        )

        # Invoke provider
        response = self.provider.ask(prompt, system_instruction=system_instruction)

        # Validate response complies with the constraints protocol
        validate_protocol(response)

        return response
