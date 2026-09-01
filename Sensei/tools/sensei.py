import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# vertexai imports are lazy-loaded in ask_vertex to avoid test suite collection errors



PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "project-62238635-aae4-41f4-880")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")


def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
    except Exception as e:
        return f"[command failed: {cmd}]\n{e}"


def repo_snapshot():
    parts = []
    parts.append("## tree -L 4\n" + run("tree -L 4 2>/dev/null || find . -maxdepth 4 -print"))
    parts.append("## python files\n" + run("find . -name '*.py'"))
    parts.append("## symbols/imports\n" + run("grep -R \"^class \\|^def \\|^from \\|^import \" . --include='*.py' 2>/dev/null | head -2000"))
    return "\n\n".join(parts)


SYSTEM_INSTRUCTION = (
    "Sen Sensei agentsın. Kendi kullanacağın araçları ve DAW terminolojisini tanırsın. "
    "Müzikal sohbet edebilir, müzik terimlerini, türleri (genre) ve bağlamları anlayabilirsin. "
    "Kendi araçlarındaki eksiklikleri fark edip geliştirme önerileri sunabilirsin. KESİNLİKLE kod yazamazsın.\n\n"
    "İnternet araştırması yaparken yalnızca kamuya açık Ableton ile ilgili belgeleri (ableton.com, help.ableton.com, www.ableton.com) okuyabilirsin. "
    "Yalnızca şu konulara izin verilmiştir: Ableton Live/Max for Live belgeleri, Live API referansları, MIDI klipleri, Drum Rack, Groove Pool, Remote Scripts, Python kontrol yüzeyi betikleri ve kamuya açık Ableton dosya biçimleri. "
    "Genel web araştırması, rastgele bloglar, StackOverflow, kod üretimi, ilgisiz siteleri kazımak veya bağlı bir arama aracı olmadan web araştırması yaptığını idtia etmek kesinlikle yasaktır. "
    "Eğer bir arama/araştırma aracı mevcut değilse, kesinlikle şu cümleyi söylemelisin: \"Web research tool unavailable.\"\n\n"
    "You are Sensei agent. You know the tools you use and DAW terminology. "
    "You can engage in musical conversations, understand music terms, genres, and contexts. "
    "You can identify deficiencies in your own tools and present development proposals. You CANNOT write code.\n\n"
    "When doing web research, you may ONLY read public Ableton-related documentation from allowed domains (ableton.com, help.ableton.com, www.ableton.com). "
    "Allowed topics are: Ableton Live/Max for Live documentation, Live API references, MIDI clips, Drum Rack, Groove Pool, Remote Scripts, Python control surface scripts, and publicly documented Ableton file formats. "
    "Forbidden: general web research, random blogs, StackOverflow, code generation, scraping unrelated sites, and claiming web research without using a connected research/search tool. "
    "If a research/search tool is unavailable, you MUST say: \"Web research tool unavailable.\""
)


def ask_vertex(prompt, model_name):
    import vertexai
    from vertexai.generative_models import GenerativeModel
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = GenerativeModel(model_name, system_instruction=SYSTEM_INSTRUCTION)
    response = model.generate_content(prompt)
    return response.text or ""


def main():
    parser = argparse.ArgumentParser(prog="sensei")
    parser.add_argument("mode", choices=["ask", "analyze", "plan", "review", "test", "context", "tools"], nargs="?", default="ask")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--with-context", action="store_true", help="Include repo context in ask mode")
    parser.add_argument("--deep", action="store_true", help="Use deep context budget profile (60000 chars)")
    args, unknown_args = parser.parse_known_args()
    args.prompt = args.prompt + unknown_args

    # Handle context subcommands
    if args.mode == "context":
        if not args.prompt:
            print("Usage: python3 tools/sensei.py context [build|status|select|explain] [args]")
            sys.exit(1)
        subcmd = args.prompt[0]
        if subcmd == "build":
            from core.agent.context_node import build_index
            print("Building context index...")
            index = build_index()
            print(f"Index built successfully. {len(index)} files indexed.")
            sys.exit(0)
        elif subcmd == "status":
            from core.agent.context_node import get_index_status
            status = get_index_status()
            print(f"Index Exists: {status.get('exists')}")
            print(f"File Count: {status.get('file_count')}")
            if status.get("exists") and status.get("last_modified"):
                mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(status.get('last_modified')))
                print(f"Last Modified: {mtime}")
            sys.exit(0)
        elif subcmd == "select":
            if len(args.prompt) < 2:
                print("Error: select command requires a task description.")
                sys.exit(1)
            task_text = " ".join(args.prompt[1:])
            from core.agent.context_node import select_context
            selected_files, context_text, selection_reason, selected_metadata = select_context(task_text, "plan", deep=args.deep)
            print("=== Selected Files ===")
            for f in selected_files:
                meta = selected_metadata[f]
                trunc_str = "True" if meta["truncated"] else "False"
                dep_reason = meta["dependency_reason"]
                dep_part = f", dependency_reason: {dep_reason}" if dep_reason else ""
                print(f"- {f} (score: {meta['score']}, reason: {meta['reason']}{dep_part}, truncated: {trunc_str})")
            
            budget_info = selected_metadata.get("_budget", {})
            print(f"\nbudget profile: {budget_info.get('profile', 'plan')}")
            print(f"budget chars: {budget_info.get('chars', 20000)}")
            print(f"used chars: {len(context_text)}")
            print(f"selected files count: {len(selected_files)}")
            sys.exit(0)
        elif subcmd == "explain":
            if len(args.prompt) < 2:
                print("Error: explain command requires a task description.")
                sys.exit(1)
            task_text = " ".join(args.prompt[1:])
            from core.agent.context_node import select_context
            selected_files, context_text, selection_reason, selected_metadata = select_context(task_text, "plan", deep=args.deep)
            print("=== Context Explanation ===")
            print(f"Task: '{task_text}'\n")
            print("Selected Files Details:")
            for f in selected_files:
                meta = selected_metadata[f]
                print(f"File: {f}")
                print(f"  - Match Score: {meta['score']}")
                print(f"  - Selection Reason: {meta['reason']}")
                if meta['dependency_reason']:
                    print(f"  - Dependency Chain: {meta['dependency_reason']}")
                print(f"  - Truncated in Context: {'Yes' if meta['truncated'] else 'No'}")
                print()
            sys.exit(0)
        else:
            print(f"Unknown context subcommand: {subcmd}")
            sys.exit(1)

    # Handle tools subcommands
    if args.mode == "tools":
        if not args.prompt:
            print("Usage: python3 tools/sensei.py tools [list|info|run] [args]")
            sys.exit(1)
        subcmd = args.prompt[0]
        from core.agent.permissions import PermissionsManager
        perms = PermissionsManager("tools")
        
        if subcmd == "list":
            from core.agent.tools import tool_registry, execution_registry
            print("=== Available Tools ===")
            for t in tool_registry.list_tools() + execution_registry.list_tools():
                allowed = perms.is_tool_allowed(t.name)
                allowed_str = "Allowed" if allowed else "Denied"
                print(f"- {t.name}: {t.description} ({allowed_str})")
            sys.exit(0)
            
        elif subcmd == "info":
            if len(args.prompt) < 2:
                print("Error: info subcommand requires a tool name.")
                sys.exit(1)
            tname = args.prompt[1]
            from core.agent.tools import tool_registry, execution_registry
            t = tool_registry.get(tname) or execution_registry.get(tname)
            if not t:
                print(f"Error: tool '{tname}' not found.")
                sys.exit(1)
            allowed = perms.is_tool_allowed(t.name)
            allowed_str = "Allowed" if allowed else "Denied"
            print(f"Tool Name: {t.name}")
            print(f"Description: {t.description}")
            print(f"Status: {allowed_str}")
            sys.exit(0)
            
        elif subcmd == "run":
            if len(args.prompt) < 2:
                print("Error: run subcommand requires a tool name.")
                sys.exit(1)
            tname = args.prompt[1]
            from core.agent.tools import tool_registry, execution_registry
            t = tool_registry.get(tname) or execution_registry.get(tname)
            if not t:
                print(f"Error: tool '{tname}' not found.")
                sys.exit(1)
                
            if not perms.is_tool_allowed(t.name):
                print(f"Permission Error: tool '{tname}' is not allowed in tools mode.")
                sys.exit(1)
                
            kwargs = {}
            run_args = args.prompt[2:]
            i = 0
            while i < len(run_args):
                arg = run_args[i]
                if arg.startswith("--"):
                    key = arg[2:]
                    if i + 1 < len(run_args):
                        val = run_args[i+1]
                        kwargs[key] = val
                        i += 2
                    else:
                        kwargs[key] = True
                        i += 1
                else:
                    i += 1
            try:
                result = t.run(**kwargs)
                print(result)
                sys.exit(0)
            except Exception as e:
                print(f"Execution Error: {e}")
                sys.exit(1)
        else:
            print(f"Unknown tools subcommand: {subcmd}")
            sys.exit(1)

    arg_prompt = " ".join(args.prompt).strip()
    try:
        stdin_prompt = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    except Exception:
        stdin_prompt = ""
    task = "\n\n".join(p for p in [stdin_prompt, arg_prompt] if p).strip()

    context = ""
    selected_files_count = 0
    index_used = False

    if args.mode == "analyze":
        from core.agent.context_node import get_index_summary
        context = get_index_summary()
        index_used = True
        selected_files_count = 0
        task = (
            "Sensei repo index summary aşağıda.\n"
            "Kod yazma. Patch önerme. Sadece mimari analiz yap: entry point, import ilişkileri, duplicate görevler, legacy katmanlar, riskler.\n\n"
            + context
        )
    elif args.mode == "plan":
        from core.agent.context_node import select_context
        if not task:
            print("Prompt boş.")
            sys.exit(1)
        selected_files, context, selection_reason, selected_metadata = select_context(task, "plan", deep=args.deep)
        selected_files_count = len(selected_files)
        index_used = True
        task = (
            "Sensei context-selected repo files aşağıda.\n"
            "Kod yazma. Patch yazma. Sadece implementation plan üret.\n\n"
            f"Kullanıcı görevi:\n{task}\n\n"
            + context
        )
    elif args.mode == "review":
        context = run("git diff -- .")
        selected_files_count = 0
        index_used = False
        task = (
            "Aşağıda git diff var.\n"
            "Kod yazma. Sadece riskleri, eksik testleri ve olası regresyonları incele.\n\n"
            + context
            + "\n\nEk not:\n"
            + task
        )
    elif args.mode == "test":
        context = run("pytest -q")
        selected_files_count = 0
        index_used = False
        task = (
            "Aşağıda pytest -q çıktısı var.\n"
            "Kod yazma. Sadece test başarısızlıklarını, hata mesajlarını ve olası çözüm yollarını analiz et.\n\n"
            + context
            + (f"\n\nKullanıcı notu:\n{task}" if task else "")
        )
    elif args.mode == "ask":
        if args.with_context:
            from core.agent.context_node import select_context
            if not task:
                print("Prompt boş.")
                sys.exit(1)
            selected_files, context, selection_reason, selected_metadata = select_context(task, "ask", deep=args.deep)
            selected_files_count = len(selected_files)
            index_used = True
            task = task + "\n\n=== Context ===\n" + context
        else:
            selected_files_count = 0
            index_used = False

    if not task:
        print("Prompt boş.")
        sys.exit(1)

    index_used_str = "true" if index_used else "false"
    print(
        f"[sensei diagnostic] mode={args.mode} "
        f"provider=vertex "
        f"model={args.model} "
        f"selected_files={selected_files_count} "
        f"context_chars={len(task)} "
        f"index_used={index_used_str}",
        file=sys.stderr
    )
    
    from core.agent.runtime import SenseiAgent
    from core.agent.providers.vertex import VertexLLMProvider
    
    provider = VertexLLMProvider()
    provider.model_name = args.model
    agent = SenseiAgent(args.mode, provider=provider)
    
    try:
        response = agent.run(task, with_context=False)
        print(response)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
