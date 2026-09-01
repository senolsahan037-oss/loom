from __future__ import annotations

import os
import json
import ast
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Set of directory names to exclude during indexing
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "exports",
    "data"
}

# Set of extensions to exclude as cache/binary files
EXCLUDE_EXTS = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pkl",
    ".bin",
    ".adg",
    ".alc",
    ".agr",
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".wav",
    ".mp3",
    ".zip",
    ".tar",
    ".gz"
}

def get_workspace_root() -> Path:
    """Finds the workspace root (2 levels up from core/agent/context_node.py)."""
    return Path(__file__).resolve().parents[2]

def get_index_path() -> Path:
    """Returns the absolute path to the data/agent_index.json file."""
    return get_workspace_root() / "data" / "agent_index.json"

def should_exclude(path: Path, root: Path) -> bool:
    """Determines whether a file or directory path should be excluded from index."""
    try:
        rel_path = path.relative_to(root)
    except ValueError:
        rel_path = path
        
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
            
    if path.name == ".DS_Store" or path.suffix.lower() in EXCLUDE_EXTS:
        return True
        
    return False

def extract_python_symbols(content: str) -> List[str]:
    """Parses Python code using AST and extracts class, function, and import names."""
    symbols = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"def {node.name}")
        elif isinstance(node, ast.Import):
            for name in node.names:
                symbols.append(f"import {name.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for name in node.names:
                symbols.append(f"from {module} import {name.name}")
    return symbols

def extract_python_imports(content: str) -> List[str]:
    """Parses Python code and extracts raw imported module names."""
    imports = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.append(name.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level
            module_str = "." * level + module
            imports.append(module_str)
    return list(set(imports))

def resolve_import_to_path(import_module: str, current_file: str, all_files: set) -> str | None:
    """Resolves an import module name to a workspace relative path if it exists in the workspace."""
    leading_dots = 0
    # Count and strip leading dots for relative imports
    while import_module.startswith('.'):
        leading_dots += 1
        import_module = import_module[1:]
        
    if leading_dots > 0:
        parts = Path(current_file).parent.parts
        levels_up = leading_dots - 1
        if levels_up < len(parts):
            base_dir_parts = parts[:-levels_up] if levels_up > 0 else parts
            base_dir = Path(*base_dir_parts)
            resolved_module = (base_dir / import_module.replace('.', '/')).as_posix()
        else:
            return None
    else:
        resolved_module = import_module.replace('.', '/')
        
    resolved_module = resolved_module.rstrip('/')
    
    # Try <module>.py
    candidate_py = resolved_module + ".py"
    if candidate_py in all_files:
        return candidate_py
        
    # Try <module>/__init__.py
    candidate_init = resolved_module + "/__init__.py"
    if candidate_init in all_files:
        return candidate_init
        
    return None

def extract_markdown_summary(content: str) -> str:
    """Extracts the first H1 and first few H2 headings for a short topic summary."""
    lines = content.splitlines()
    h1 = None
    subheadings = []
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            if not h1:
                h1 = line[2:].strip()
        elif line.startswith("## "):
            subheadings.append(line[3:].strip())
            
    if h1:
        if subheadings:
            return f"{h1} - Subheadings: {', '.join(subheadings[:4])}"
        return h1
    elif subheadings:
        return f"Topics: {', '.join(subheadings[:4])}"
    return ""

def build_index() -> Dict[str, Any]:
    """Walks the workspace, indexes files, and saves index to data/agent_index.json using incremental logic."""
    root = get_workspace_root()
    index_path = get_index_path()
    
    # Load old index if exists for incremental build
    old_index = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                old_index = json.load(f)
        except Exception:
            pass
            
    index_data = {}
    ast_errors = []
    reused_count = 0
    parsed_count = 0
    
    # Pass 1: find all eligible files in the workspace (we need them to resolve imports)
    all_eligible_files = set()
    for dirpath, dirnames, filenames in os.walk(root):
        # Exclude directories in-place during walk
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if should_exclude(fpath, root):
                continue
            rel_path = fpath.relative_to(root).as_posix()
            all_eligible_files.add(rel_path)
            
    # Pass 2: build metadata and parse symbols / imports
    for rel_path in sorted(all_eligible_files):
        fpath = root / rel_path
        try:
            stat = fpath.stat()
            file_size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            continue
            
        # Check if file has not changed to reuse cached metadata
        cached = old_index.get(rel_path)
        if (cached and isinstance(cached, dict) 
                and cached.get("size") == file_size 
                and cached.get("mtime") == mtime
                and "symbols" in cached
                and "dependencies" in cached):
            index_data[rel_path] = cached
            reused_count += 1
            continue
            
        symbols = []
        summary = ""
        dependencies = []
        parsed_count += 1
        
        # Read content to parse symbols or summaries
        if fpath.suffix.lower() == ".py":
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                ast_errors.append(f"{rel_path}: Read error: {e}")
                content = ""
                
            if content:
                # Check for syntax/parsing error
                try:
                    ast.parse(content)
                except SyntaxError as se:
                    ast_errors.append(f"{rel_path}: AST Parse Error: {se}")
                    
                symbols = extract_python_symbols(content)
                raw_imports = extract_python_imports(content)
                for imp in raw_imports:
                    dep_path = resolve_import_to_path(imp, rel_path, all_eligible_files)
                    if dep_path and dep_path != rel_path:
                        dependencies.append(dep_path)
                dependencies = sorted(list(set(dependencies)))
        elif fpath.suffix.lower() == ".md":
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                summary = extract_markdown_summary(content)
            except Exception as e:
                ast_errors.append(f"{rel_path}: Read/Parse error: {e}")
                
        index_data[rel_path] = {
            "size": file_size,
            "mtime": mtime,
            "symbols": symbols,
            "summary": summary,
            "dependencies": dependencies
        }
        
    index_data["_diagnostics"] = {
        "ast_errors": ast_errors,
        "reused_count": reused_count,
        "parsed_count": parsed_count
    }
    
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)
        
    return index_data

def get_index_status() -> Dict[str, Any]:
    """Returns metadata about the index file."""
    index_path = get_index_path()
    if not index_path.exists():
        return {"exists": False, "file_count": 0, "last_modified": 0.0}
        
    try:
        stat = index_path.stat()
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Exclude _diagnostics from file count
        file_count = len([k for k in data.keys() if not k.startswith("_")])
        return {
            "exists": True,
            "file_count": file_count,
            "last_modified": stat.st_mtime,
            "size": stat.st_size
        }
    except Exception:
        return {"exists": True, "error": "failed to parse index", "file_count": 0}

def get_index_summary() -> str:
    """Returns a broader repo summary based on the index data."""
    index_path = get_index_path()
    if not index_path.exists():
        build_index()
        
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return "Failed to load repo index for summary."
        
    summary_lines = []
    summary_lines.append("=== Repo Index Summary ===")
    for rel_path, meta in sorted(index.items()):
        if rel_path.startswith("_"):
            continue
        summary_lines.append(f"\nFile: {rel_path} ({meta.get('size', 0)} bytes)")
        if meta.get("summary"):
            summary_lines.append(f"  Summary: {meta['summary']}")
        symbols = meta.get("symbols", [])
        if symbols:
            # Show top 15 symbols to keep it concise
            symbols_to_show = symbols[:15]
            summary_lines.append(f"  Symbols: {', '.join(symbols_to_show)}")
            if len(symbols) > 15:
                summary_lines.append(f"  ... and {len(symbols) - 15} more symbols")
                
    return "\n".join(summary_lines)

def tokenize_task(task: str) -> set:
    """Helper to tokenize task into meaningful keywords including Turkish stop words."""
    task_clean = task.lower()
    for char in ['.', ',', '/', '\\', '-', '_', ':', ';', '(', ')', '[', ']', '{', '}', '\'', '"', '?', '!']:
        task_clean = task_clean.replace(char, ' ')
        
    words = task_clean.split()
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'if', 'then', 'else', 'when',
        'at', 'by', 'for', 'from', 'in', 'into', 'of', 'off', 'on', 'onto',
        'out', 'over', 'to', 'up', 'with', 'is', 'was', 'were', 'be', 'been',
        'am', 'are', 'this', 'that', 'these', 'those', 'it', 'its', 'we', 'us',
        'our', 'you', 'your', 'they', 'them', 'their', 'he', 'him', 'his',
        'she', 'her', 'how', 'what', 'why', 'where', 'who', 'which', 'can',
        'will', 'would', 'should', 'could', 'may', 'might', 'must', 'only',
        # Turkish stop words
        'bir', 've', 'veya', 'ama', 'ise', 'için', 'gibi', 'ile', 'en', 'daha',
        'ki', 'da', 'de', 'mu', 'mı', 'mi', 'mü', 'bu', 'şu', 'o', 'ne', 'nasıl',
        'neden', 'niçin', 'çünkü'
    }
    return {w for w in words if len(w) > 1 and w not in stop_words}

def select_context(task: str, mode: str, max_chars: int = None, deep: bool = False) -> Tuple[List[str], str, str, Dict[str, Dict[str, Any]]]:
    """Selects files matching the task, expands dependencies, and constructs context metadata with budget profiles."""
    index_path = get_index_path()
    if not index_path.exists():
        index = build_index()
    else:
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            index = build_index()
            
    task_lower = task.lower()
    mode_lower = mode.lower()
    
    # 1. Resolve budget profiles
    if deep or mode_lower == "deep":
        budget_profile = "deep"
        budget_chars = 60000
    else:
        if mode_lower == "plan":
            budget_profile = "plan"
            budget_chars = 20000
        elif mode_lower == "analyze":
            budget_profile = "analyze"
            budget_chars = 40000
        elif mode_lower == "ask":
            # If select_context is called in ask mode, it means --with-context was passed, so we use default (8000 chars)
            budget_profile = "default"
            budget_chars = 8000
        else:
            budget_profile = "default"
            budget_chars = 8000
            
    # Allow explicit max_chars to override profile limit
    if max_chars is not None:
        budget_chars = max_chars
        
    # 2. Classify task type
    agent_keywords = ["agent", "sensei", "context", "cli", "prompt"]
    is_agent_task = any(kw in task_lower or kw in mode_lower for kw in agent_keywords)
    is_test_task = any(kw in task_lower for kw in ["test", "pytest", "testing", "mock"])
    
    # Check if task mentions docs, protocol, architecture, roadmap
    docs_keywords = ["docs", "protocol", "architecture", "roadmap"]
    is_docs_mentioned = any(kw in task_lower for kw in docs_keywords)
    
    # Check if taxonomy is explicitly allowed
    is_taxonomy_allowed = any(kw in task_lower for kw in ["dataset", "taxonomy", "capabilities"])
    
    # 3. Add always-include files (reasons are set dynamically)
    always_files = []
    always_reasons = {}
    
    # For agent tasks: tools/sensei.py and core/agent/*
    if is_agent_task:
        if "tools/sensei.py" in index:
            always_files.append("tools/sensei.py")
            always_reasons["tools/sensei.py"] = "always included for agent tasks"
        for rel_path in index:
            if rel_path.startswith("core/agent/"):
                always_files.append(rel_path)
                always_reasons[rel_path] = "always included for agent tasks (core/agent/*)"
                
    # For agent/test tasks: tests/test_sensei_agent_*.py
    if is_agent_task and is_test_task:
        for rel_path in index:
            if rel_path.startswith("tests/test_sensei_agent_"):
                always_files.append(rel_path)
                always_reasons[rel_path] = "included for agent/test task"
                
    # 4. Match task keywords to file paths and symbols (and calculate scores)
    keywords = tokenize_task(task)
    if not keywords:
        keywords = set(task_lower.split())
        
    keyword_scores = {}
    for rel_path, meta in index.items():
        if rel_path.startswith("_"):
            continue
            
        # Stricter DatasetRoot/taxonomy filtering
        if (rel_path.startswith("DatasetRoot/taxonomy/") or "DatasetRoot/taxonomy" in rel_path) and not is_taxonomy_allowed:
            continue
            
        # Enforce docs restriction
        if rel_path.startswith("docs/") and not is_docs_mentioned:
            continue
            
        # Enforce tests/test_sensei_agent_*.py restriction
        if rel_path.startswith("tests/test_sensei_agent_") and not (is_agent_task and is_test_task):
            continue
            
        score = 0
        rel_path_lower = rel_path.lower()
        file_name_lower = Path(rel_path).name.lower()
        
        for kw in keywords:
            # Check filename / path
            if kw in file_name_lower:
                score += 15
            elif kw in rel_path_lower:
                score += 5
                
            # Check symbols (at most once per keyword)
            symbols = meta.get("symbols", [])
            symbol_matched = False
            for sym in symbols:
                if kw in sym.lower():
                    symbol_matched = True
                    break
            if symbol_matched:
                score += 10
                
            # Check markdown summary (at most once per keyword)
            summary = meta.get("summary", "")
            if summary and kw in summary.lower():
                score += 3
                
        # Deprioritize test files if this is not a test task
        if rel_path.startswith("tests/") and not is_test_task:
            score -= 15
            
        if score > 0:
            keyword_scores[rel_path] = score
            
    # Relevance-first selection: Only matched files with score >= 10
    matched_files = [f for f, s in keyword_scores.items() if s >= 10]
    matched_files.sort(key=lambda x: keyword_scores[x], reverse=True)
    
    # Early stopping / sufficiency cutoff:
    if matched_files:
        top_score = keyword_scores[matched_files[0]]
        # Only keep files with score >= max(10, top_score * 0.5)
        cutoff = max(10, top_score * 0.5)
        matched_files = [f for f in matched_files if keyword_scores[f] >= cutoff]
        # Cap to at most 3 matches
        matched_files = matched_files[:3]
        
    # 5. README.md conditional inclusion policy
    readme_keywords = {"overview", "architecture", "roadmap", "docs", "protocol"}
    is_readme_requested = any(kw in task_lower for kw in readme_keywords)
    
    include_readme = False
    readme_reason = ""
    if is_readme_requested:
        include_readme = True
        readme_reason = "included (explicit keyword match)"
    elif mode_lower == "analyze" or budget_profile == "analyze":
        include_readme = True
        readme_reason = "included (analyze mode default)"
    elif len(matched_files) == 0:
        include_readme = True
        readme_reason = "included as fallback (no relevant files found)"
        
    if include_readme and "README.md" in index:
        readme_meta = index["README.md"]
        if readme_meta.get("size", 0) < 15000:
            if "README.md" not in always_files:
                # Add to the beginning of always files
                always_files.insert(0, "README.md")
                always_reasons["README.md"] = readme_reason
                
    # 6. Gather one-hop dependency expansion details
    parent_to_deps = {}
    for parent in always_files + matched_files:
        meta = index.get(parent, {})
        deps = meta.get("dependencies", [])
        valid_deps = []
        for dep in deps:
            if dep.startswith("docs/") and not is_docs_mentioned:
                continue
            if dep.startswith("tests/test_sensei_agent_") and not (is_agent_task and is_test_task):
                continue
            if (dep.startswith("DatasetRoot/taxonomy/") or "DatasetRoot/taxonomy" in dep) and not is_taxonomy_allowed:
                continue
            if dep not in always_files and dep not in matched_files:
                valid_deps.append(dep)
        parent_to_deps[parent] = valid_deps
        
    # 7. Assemble ordered files to attempt including
    files_to_attempt = []
    reasons = {}
    scores = {}
    dependency_reasons = {}
    
    for f in always_files:
        if f not in reasons:
            files_to_attempt.append(f)
            reasons[f] = always_reasons.get(f, "required file")
            scores[f] = keyword_scores.get(f, 0)
            dependency_reasons[f] = ""
            
        for dep in parent_to_deps.get(f, []):
            if dep not in reasons:
                files_to_attempt.append(dep)
                reasons[dep] = "one-hop dependency"
                scores[dep] = keyword_scores.get(dep, 0)
                dependency_reasons[dep] = f"imported by {f}"
                
    for f in matched_files:
        if f not in reasons:
            files_to_attempt.append(f)
            reasons[f] = "matched keywords"
            scores[f] = keyword_scores.get(f, 0)
            dependency_reasons[f] = ""
            
        for dep in parent_to_deps.get(f, []):
            if dep not in reasons:
                files_to_attempt.append(dep)
                reasons[dep] = "one-hop dependency"
                scores[dep] = keyword_scores.get(dep, 0)
                dependency_reasons[dep] = f"imported by {f}"
                
    # 8. Construct final selection and context text
    selected_files = []
    selected_metadata = {}
    context_text_parts = []
    current_chars = 0
    workspace_root = get_workspace_root()
    
    for f in files_to_attempt:
        abs_path = workspace_root / f
        if not abs_path.exists():
            continue
            
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as file_handle:
                content = file_handle.read()
        except Exception:
            continue
            
        header = f"\n\n================================================================================\nFILE: {f}\n================================================================================\n"
        needed = len(header)
        if current_chars + needed >= budget_chars:
            break
            
        available = budget_chars - current_chars - needed
        if len(content) <= available:
            context_text_parts.append(header + content)
            current_chars += needed + len(content)
            selected_files.append(f)
            selected_metadata[f] = {
                "score": scores[f],
                "reason": reasons[f],
                "dependency_reason": dependency_reasons[f],
                "truncated": False
            }
        else:
            # Try to add truncated content
            if available > 500:
                truncated_content = content[:available - 50] + "\n\n[Content truncated due to max_chars limit...]\n"
                context_text_parts.append(header + truncated_content)
                current_chars += needed + len(truncated_content)
                selected_files.append(f)
                selected_metadata[f] = {
                    "score": scores[f],
                    "reason": reasons[f],
                    "dependency_reason": dependency_reasons[f],
                    "truncated": True
                }
            break
            
    # Store the budget information in the metadata returned
    selected_metadata["_budget"] = {
        "profile": budget_profile,
        "chars": budget_chars
    }
    
    # Format selection reasons
    reason_lines = []
    for f in selected_files:
        meta = selected_metadata[f]
        score_str = f"score: {meta['score']}"
        trunc_str = "truncated: True" if meta["truncated"] else "truncated: False"
        dep_str = f"dependency: {meta['dependency_reason']}" if meta["dependency_reason"] else ""
        details = [meta["reason"], score_str, dep_str, trunc_str]
        details_str = ", ".join([d for d in details if d])
        reason_lines.append(f"- {f}: {details_str}")
    selection_reason = "\n".join(reason_lines)
    
    context_text = "".join(context_text_parts).strip()
    return selected_files, context_text, selection_reason, selected_metadata
