import asyncio
import json
import os
import re
import yaml
from pocketflow import Node, BatchNode, AsyncParallelBatchNode
from utils.crawl_github_files import crawl_github_files, GitHubCrawlError, parse_github_http_url
from utils.call_llm import call_llm
from utils.crawl_local_files import crawl_local_files
from utils.errors import format_error
from utils.progress import emit_step
from pathlib import Path

from utils.repo_map import build_repo_map, expand_abstraction_files


def _upstream_commit(local_dir):
    if not local_dir:
        return None
    try:
        from utils.stale import git_head

        return git_head(local_dir)
    except Exception:
        return None

# Chapters are written concurrently; keep enough headroom that the provider does not
# start rate-limiting, which would cost more than the parallelism saves.
CHAPTER_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "5"))
YAML_TEMPERATURE = 0.2
CRAWL_FILE_THRESHOLD = int(os.getenv("CRAWL_FILE_THRESHOLD", "80"))
MIN_CHAPTER_CHARS = 200
DEFAULT_LLM_CONTEXT_CHARS = 100_000
DEFAULT_LLM_FILE_CHARS = 6_000
_YAML_FENCE = re.compile(r"```(?:yaml|yml)[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def _repair_mojibake(text: str) -> str:
    if not text or not any(0x80 <= ord(ch) <= 0x9F for ch in text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text


def _strip_yaml_controls(text: str) -> str:
    return "".join(
        ch if ch in "\n\t\r" or (ord(ch) >= 32 and not (0x7F <= ord(ch) <= 0x9F)) else " "
        for ch in text
    )


def parse_llm_yaml(response: str):
    """Load YAML from an LLM reply, including fenced blocks and mojibake."""
    text = _repair_mojibake(response or "")
    match = _YAML_FENCE.search(text)
    if match:
        yaml_str = match.group(1).strip()
    else:
        stripped = text.strip()
        yaml_str = stripped
        for tag in ("```yaml", "```yml", "```YAML", "```"):
            if tag in stripped:
                yaml_str = stripped.split(tag, 1)[1].split("```", 1)[0].strip()
                break
    yaml_str = _strip_yaml_controls(yaml_str)
    return yaml.safe_load(yaml_str)


def _context_limits():
    return (
        int(os.getenv("LLM_CONTEXT_CHARS", str(DEFAULT_LLM_CONTEXT_CHARS))),
        int(os.getenv("LLM_FILE_CHARS", str(DEFAULT_LLM_FILE_CHARS))),
    )


def build_code_context(
    files_data,
    *,
    max_total_chars=None,
    max_file_chars=None,
):
    """Pack file contents for an LLM prompt without overflowing the context window.

    File indices stay aligned with `files_data` so later nodes can still reference
    every path. Bodies are dropped once the character budget is spent.
    """
    if max_total_chars is None or max_file_chars is None:
        default_total, default_file = _context_limits()
        if max_total_chars is None:
            max_total_chars = default_total
        if max_file_chars is None:
            max_file_chars = default_file

    file_info = [(i, path) for i, (path, _content) in enumerate(files_data)]
    parts = []
    used = 0
    files_with_content = 0
    truncated_files = 0
    for i, (path, content) in enumerate(files_data):
        body = content or ""
        omitted = len(body) > max_file_chars
        if omitted:
            body = body[:max_file_chars]
        header = f"--- File Index {i}: {path} ---\n"
        suffix = "\n... [truncated]\n\n" if omitted else "\n\n"
        piece = header + body + suffix
        if used + len(piece) > max_total_chars:
            if files_with_content == 0:
                room = max_total_chars - used - len(header) - len("\n... [truncated]\n\n")
                if room > 0:
                    parts.append(header + body[:room] + "\n... [truncated]\n\n")
                    files_with_content = 1
                    truncated_files += 1
            break
        parts.append(piece)
        used += len(piece)
        files_with_content += 1
        if omitted:
            truncated_files += 1

    context = "".join(parts)
    stats = {
        "file_count": len(files_data),
        "files_with_content": files_with_content,
        "truncated_files": truncated_files,
        "chars": len(context),
        "max_total_chars": max_total_chars,
    }
    return context, file_info, stats


def clip_snippets(content_map, *, max_total_chars=None, max_file_chars=None):
    text, _stats = clip_snippets_with_stats(
        content_map, max_total_chars=max_total_chars, max_file_chars=max_file_chars
    )
    return text


def clip_snippets_with_stats(content_map, *, max_total_chars=None, max_file_chars=None):
    if max_total_chars is None or max_file_chars is None:
        default_total, default_file = _context_limits()
        if max_total_chars is None:
            max_total_chars = default_total
        if max_file_chars is None:
            max_file_chars = default_file
    parts = []
    used = 0
    included = 0
    truncated_files = 0
    items = list((content_map or {}).items())
    for idx_path, content in items:
        raw = content or ""
        cut = len(raw) > max_file_chars
        body = raw[:max_file_chars]
        label = idx_path.split("# ", 1)[1] if "# " in idx_path else idx_path
        piece = f"--- File: {label} ---\n*source: {label}*\n{body}\n\n"
        if used + len(piece) > max_total_chars:
            truncated_files += len(items) - included
            break
        parts.append(piece)
        used += len(piece)
        included += 1
        if cut:
            truncated_files += 1
    omitted = max(0, len(items) - included)
    stats = {
        "file_count": len(items),
        "files_used": included,
        "truncated_files": truncated_files,
        "omitted_files": omitted,
    }
    return "".join(parts).rstrip(), stats


TRUNCATION_MARKER_RE = re.compile(r"<!--\s*qs:truncated_files=(\d+)\s+total=(\d+)\s*-->")


def inject_truncation_note(text: str, stats: dict) -> str:
    """Record how many cited files were clipped so the reading page can show it."""
    n = int((stats or {}).get("truncated_files") or 0)
    total = int((stats or {}).get("file_count") or 0)
    marker = f"<!-- qs:truncated_files={n} total={total} -->"
    note = f"> **引用截断：** 本章引用了 {total} 个文件，其中 {n} 个因长度限制被截断。"
    body = (text or "").lstrip()
    if TRUNCATION_MARKER_RE.search(body):
        body = TRUNCATION_MARKER_RE.sub(marker, body, count=1)
        return body
    lines = body.split("\n", 1)
    if lines and lines[0].startswith("#"):
        rest = lines[1] if len(lines) > 1 else ""
        return f"{lines[0]}\n\n{marker}\n\n{note}\n{rest}"
    return f"{marker}\n\n{note}\n\n{body}"


def parse_truncation_note(text: str) -> dict | None:
    match = TRUNCATION_MARKER_RE.search(text or "")
    if not match:
        return None
    return {"truncated": int(match.group(1)), "total": int(match.group(2))}


def finalize_chapter(chapter_content, chapter_num, abstraction_name):
    actual_heading = f"# Chapter {chapter_num}: {abstraction_name}"
    text = (chapter_content or "").strip()
    if not text.startswith(f"# Chapter {chapter_num}"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("#"):
            lines[0] = actual_heading
            text = "\n".join(lines)
        else:
            text = f"{actual_heading}\n\n{text}"
    if len(text) < MIN_CHAPTER_CHARS:
        raise ValueError(
            f"Chapter {chapter_num} is too short ({len(text)} chars); expected at least {MIN_CHAPTER_CHARS}"
        )
    from utils.learn_outcomes import inject_outcomes

    return inject_outcomes(text)


# Helper to get content for specific file indices
def get_content_for_indices(files_data, indices):
    content_map = {}
    for i in indices:
        if 0 <= i < len(files_data):
            path, content = files_data[i]
            content_map[f"{i} # {path}"] = (
                content  # Use index + path as key for context
            )
    return content_map


class FetchRepo(Node):
    def prep(self, shared):
        repo_url = shared.get("repo_url")
        local_dir = shared.get("local_dir")
        project_name = shared.get("project_name")

        if not project_name:
            if repo_url:
                try:
                    _owner, repo_name, _remainder = parse_github_http_url(repo_url)
                    project_name = repo_name
                except GitHubCrawlError:
                    project_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
            else:
                project_name = os.path.basename(os.path.abspath(local_dir))
            shared["project_name"] = project_name

        # Get file patterns directly from shared
        include_patterns = shared["include_patterns"]
        exclude_patterns = shared["exclude_patterns"]
        max_file_size = shared["max_file_size"]

        return {
            "repo_url": repo_url,
            "local_dir": local_dir,
            "token": shared.get("github_token"),
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "max_file_size": max_file_size,
            "use_relative_paths": True,
            "include_specified": bool(shared.get("include_specified")),
        }

    def exec(self, prep_res):
        emit_step("fetch")
        from utils.crawl_cache import load_crawl_cache, save_crawl_cache
        from utils.crawl_progress import crawl_progress
        from utils.gitlab_gitea import classify_repo_url

        cached = load_crawl_cache(prep_res)
        if cached:
            print(crawl_progress("cache", count=len(cached)))
            files_list = cached
        else:
            if prep_res["repo_url"]:
                print(crawl_progress("start", path=prep_res["repo_url"]))
                kind = classify_repo_url(prep_res["repo_url"]) or "github"
                if kind in {"gitlab", "gitea"}:
                    import tempfile

                    from utils.disk_warn import temp_clone_warning
                    from utils.gitlab_gitea import clone_http_repo

                    warn = temp_clone_warning()
                    if warn:
                        print(f"QUICK_STUDY_WARN: {warn}")
                    tmp = tempfile.mkdtemp(prefix="qs-clone-")
                    local = clone_http_repo(prep_res["repo_url"], tmp, token=prep_res.get("token"))
                    result = crawl_local_files(
                        directory=str(local),
                        include_patterns=prep_res["include_patterns"],
                        exclude_patterns=prep_res["exclude_patterns"],
                        max_file_size=prep_res["max_file_size"],
                        use_relative_paths=True,
                    )
                else:
                    try:
                        result = crawl_github_files(
                            repo_url=prep_res["repo_url"],
                            token=prep_res["token"],
                            include_patterns=prep_res["include_patterns"],
                            exclude_patterns=prep_res["exclude_patterns"],
                            max_file_size=prep_res["max_file_size"],
                            use_relative_paths=prep_res["use_relative_paths"],
                        )
                    except GitHubCrawlError as exc:
                        raise ValueError(format_error(exc)) from exc
            else:
                print(crawl_progress("start", path=prep_res["local_dir"]))
                result = crawl_local_files(
                    directory=prep_res["local_dir"],
                    include_patterns=prep_res["include_patterns"],
                    exclude_patterns=prep_res["exclude_patterns"],
                    max_file_size=prep_res["max_file_size"],
                    use_relative_paths=prep_res["use_relative_paths"]
                )
            if not isinstance(result, dict):
                raise ValueError(format_error("crawl returned no result for this URL"))
            if result.get("stats", {}).get("error"):
                raise ValueError(format_error(result["stats"]["error"]))
            files_list = sorted(result.get("files", {}).items(), key=lambda item: item[0])
            save_crawl_cache(prep_res, files_list)
        if len(files_list) == 0:
            raise ValueError(format_error("Failed to fetch files"))
        threshold = CRAWL_FILE_THRESHOLD
        if len(files_list) > threshold and not prep_res.get("include_specified"):
            from utils.include_suggest import suggest_include_patterns

            suggestion = suggest_include_patterns(files_list)
            raise ValueError(
                format_error(
                    f"crawled {len(files_list)} files (threshold {threshold}). "
                    "Set include / exclude / max-size to shrink the crawl, "
                    "or pass include patterns to confirm this scope. "
                    f"Suggested include: {suggestion}"
                )
            )
        print(crawl_progress("done", count=len(files_list)))
        print(f"Fetched {len(files_list)} files.")
        print(f"QUICK_STUDY_STATS: file_count={len(files_list)} map_mode=pending")
        return files_list

    def post(self, shared, prep_res, exec_res):
        shared["files"] = exec_res
        shared["file_count"] = len(exec_res)
        project_name = shared.get("project_name")
        if project_name:
            print(f"QUICK_STUDY_OUTPUT: {project_name}")


class IdentifyAbstractions(Node):
    def prep(self, shared):
        files_data = shared["files"]
        project_name = shared["project_name"]  # Get project name
        language = shared.get("language", "english")  # Get language
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True
        max_abstraction_num = shared.get("max_abstraction_num", 10)  # Get max_abstraction_num, default to 10

        context, file_info, stats = build_code_context(files_data)
        map_mode = stats["files_with_content"] < stats["file_count"]
        if map_mode:
            context, map_stats = build_repo_map(files_data)
            print(
                f"Using repo map for {map_stats['file_count']} files "
                f"({map_stats['chars']} chars, {map_stats['files_with_symbols']} with symbols)."
            )
            file_listing_for_prompt = (
                "File indices are the integers at the start of each repo-map line."
            )
        else:
            file_listing_for_prompt = "\n".join(
                [f"- {idx} # {path}" for idx, path in file_info]
            )
        from utils.learning_goal import learning_goal_block
        from utils.seed_files import seed_prompt_block

        self._learning_goal_block = learning_goal_block(shared.get("learning_goal"))
        self._seed_block = seed_prompt_block(shared.get("seed_files"))
        return (
            context,
            file_listing_for_prompt,
            len(files_data),
            project_name,
            language,
            use_cache,
            max_abstraction_num,
            map_mode,
        )

    def exec(self, prep_res):
        (
            context,
            file_listing_for_prompt,
            file_count,
            project_name,
            language,
            use_cache,
            max_abstraction_num,
            map_mode,
        ) = prep_res
        emit_step("identify")
        print(f"Identifying abstractions using LLM...")

        # Add language instruction and hints only if not English
        language_instruction = ""
        name_lang_hint = ""
        desc_lang_hint = ""
        extra_constraints = ""
        if getattr(self, "_learning_goal_block", ""):
            extra_constraints += self._learning_goal_block
        if getattr(self, "_seed_block", ""):
            extra_constraints += self._seed_block
        if language.lower() != "english":
            language_instruction = f"IMPORTANT: Generate the `name` and `description` for each abstraction in **{language.capitalize()}** language. Do NOT use English for these fields.\n\n"
            # Keep specific hints here as name/description are primary targets
            name_lang_hint = f" (value in {language.capitalize()})"
            desc_lang_hint = f" (value in {language.capitalize()})"

        prompt = f"""
For the project `{project_name}`:

Codebase Context:
{context}

{language_instruction}{extra_constraints}Analyze the codebase context.
Identify the top 5-{max_abstraction_num} core most important abstractions to help those new to the codebase.
{"The context is a repo map (paths and symbols, not full source). Do not treat every adapter, plugin, or package as its own abstraction." if map_mode else ""}

For each abstraction, provide:
1. A concise `name`{name_lang_hint}.
2. A beginner-friendly `description` explaining what it is with a simple analogy, in around 100 words{desc_lang_hint}.
3. A list of relevant `file_indices` (integers) using the format `idx # path/comment`.

List of file indices and paths present in the context:
{file_listing_for_prompt}

Format the output as a YAML list of dictionaries:

```yaml
- name: |
    Query Processing{name_lang_hint}
  description: |
    Explains what the abstraction does.
    It's like a central dispatcher routing requests.{desc_lang_hint}
  file_indices:
    - 0 # path/to/file1.py
    - 3 # path/to/related.py
- name: |
    Query Optimization{name_lang_hint}
  description: |
    Another core concept, similar to a blueprint for objects.{desc_lang_hint}
  file_indices:
    - 5 # path/to/another.js
# ... up to {max_abstraction_num} abstractions
```"""
        response = call_llm(
            prompt,
            use_cache=(use_cache and self.cur_retry == 0),
            temperature=YAML_TEMPERATURE,
            stage="identify",
        )

        from utils.identify_parse import parse_llm_structured

        abstractions = parse_llm_structured(response)

        if not isinstance(abstractions, list):
            raise ValueError("LLM Output is not a list")

        validated_abstractions = []
        for item in abstractions:
            if not isinstance(item, dict) or not all(
                k in item for k in ["name", "description", "file_indices"]
            ):
                raise ValueError(f"Missing keys in abstraction item: {item}")
            if not isinstance(item["name"], str):
                raise ValueError(f"Name is not a string in item: {item}")
            if not isinstance(item["description"], str):
                raise ValueError(f"Description is not a string in item: {item}")
            if not isinstance(item["file_indices"], list):
                raise ValueError(f"file_indices is not a list in item: {item}")

            # Validate indices
            validated_indices = []
            for idx_entry in item["file_indices"]:
                try:
                    if isinstance(idx_entry, int):
                        idx = idx_entry
                    elif isinstance(idx_entry, str) and "#" in idx_entry:
                        idx = int(idx_entry.split("#")[0].strip())
                    else:
                        idx = int(str(idx_entry).strip())

                    if not (0 <= idx < file_count):
                        raise ValueError(
                            f"Invalid file index {idx} found in item {item['name']}. Max index is {file_count - 1}."
                        )
                    validated_indices.append(idx)
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Could not parse index from entry: {idx_entry} in item {item['name']}"
                    )

            item["files"] = sorted(list(set(validated_indices)))
            # Store only the required fields
            validated_abstractions.append(
                {
                    "name": item["name"].strip(),
                    "description": item["description"].strip(),
                    "files": item["files"],
                }
            )

        print(f"Identified {len(validated_abstractions)} abstractions.")
        return validated_abstractions

    def post(self, shared, prep_res, exec_res):
        shared["abstractions"] = expand_abstraction_files(exec_res, shared["files"])
        map_mode = bool(prep_res[-1]) if isinstance(prep_res, tuple) else False
        file_count = prep_res[2] if isinstance(prep_res, tuple) else len(shared.get("files") or [])
        shared["map_mode"] = map_mode
        shared["file_count"] = file_count
        print(f"QUICK_STUDY_STATS: file_count={file_count} map_mode={str(map_mode).lower()}")


class AnalyzeRelationships(Node):
    def prep(self, shared):
        abstractions = shared[
            "abstractions"
        ]  # Now contains 'files' list of indices, name/description potentially translated
        files_data = shared["files"]
        project_name = shared["project_name"]  # Get project name
        language = shared.get("language", "english")  # Get language
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True

        # Get the actual number of abstractions directly
        num_abstractions = len(abstractions)

        # Create context with abstraction names, indices, descriptions, and relevant file snippets
        context = "Identified Abstractions:\\n"
        all_relevant_indices = set()
        abstraction_info_for_prompt = []
        for i, abstr in enumerate(abstractions):
            # Use 'files' which contains indices directly
            file_indices_str = ", ".join(map(str, abstr["files"]))
            # Abstraction name and description might be translated already
            info_line = f"- Index {i}: {abstr['name']} (Relevant file indices: [{file_indices_str}])\\n  Description: {abstr['description']}"
            context += info_line + "\\n"
            abstraction_info_for_prompt.append(
                f"{i} # {abstr['name']}"
            )  # Use potentially translated name here too
            all_relevant_indices.update(abstr["files"])

        context += "\\nRelevant File Snippets (Referenced by Index and Path):\\n"
        # Get content for relevant files using helper
        relevant_files_content_map = get_content_for_indices(
            files_data, sorted(list(all_relevant_indices))
        )
        file_context_str = clip_snippets(relevant_files_content_map)
        context += file_context_str

        return (
            context,
            "\n".join(abstraction_info_for_prompt),
            num_abstractions, # Pass the actual count
            project_name,
            language,
            use_cache,
        )  # Return use_cache

    def exec(self, prep_res):
        (
            context,
            abstraction_listing,
            num_abstractions, # Receive the actual count
            project_name,
            language,
            use_cache,
         ) = prep_res  # Unpack use_cache
        emit_step("relationships")
        print(f"Analyzing relationships using LLM...")

        # Add language instruction and hints only if not English
        language_instruction = ""
        lang_hint = ""
        list_lang_note = ""
        if language.lower() != "english":
            language_instruction = f"IMPORTANT: Generate the `summary` and relationship `label` fields in **{language.capitalize()}** language. Do NOT use English for these fields.\n\n"
            lang_hint = f" (in {language.capitalize()})"
            list_lang_note = f" (Names might be in {language.capitalize()})"  # Note for the input list

        prompt = f"""
Based on the following abstractions and relevant code snippets from the project `{project_name}`:

List of Abstraction Indices and Names{list_lang_note}:
{abstraction_listing}

Context (Abstractions, Descriptions, Code):
{context}

{language_instruction}Please provide:
1. A high-level `summary` of the project's main purpose and functionality in a few beginner-friendly sentences{lang_hint}. Use markdown formatting with **bold** and *italic* text to highlight important concepts.
2. A list (`relationships`) describing the key interactions between these abstractions. For each relationship, specify:
    - `from_abstraction`: Index of the source abstraction (e.g., `0 # AbstractionName1`)
    - `to_abstraction`: Index of the target abstraction (e.g., `1 # AbstractionName2`)
    - `label`: A brief label for the interaction **in just a few words**{lang_hint} (e.g., "Manages", "Inherits", "Uses").
    Ideally the relationship should be backed by one abstraction calling or passing parameters to another.
    Simplify the relationship and exclude those non-important ones.

IMPORTANT: Make sure EVERY abstraction is involved in at least ONE relationship (either as source or target). Each abstraction index must appear at least once across all relationships.

Format the output as YAML:

```yaml
summary: |
  A brief, simple explanation of the project{lang_hint}.
  Can span multiple lines with **bold** and *italic* for emphasis.
relationships:
  - from_abstraction: 0 # AbstractionName1
    to_abstraction: 1 # AbstractionName2
    label: "Manages"{lang_hint}
  - from_abstraction: 2 # AbstractionName3
    to_abstraction: 0 # AbstractionName1
    label: "Provides config"{lang_hint}
  # ... other relationships
```

Now, provide the YAML output:
"""
        response = call_llm(
            prompt,
            use_cache=(use_cache and self.cur_retry == 0),
            temperature=YAML_TEMPERATURE,
            stage="relationships",
        )

        relationships_data = parse_llm_yaml(response)

        if not isinstance(relationships_data, dict) or not all(
            k in relationships_data for k in ["summary", "relationships"]
        ):
            raise ValueError(
                "LLM output is not a dict or missing keys ('summary', 'relationships')"
            )
        if not isinstance(relationships_data["summary"], str):
            raise ValueError("summary is not a string")
        if not isinstance(relationships_data["relationships"], list):
            raise ValueError("relationships is not a list")

        # Validate relationships structure
        validated_relationships = []
        for rel in relationships_data["relationships"]:
            # Check for 'label' key
            if not isinstance(rel, dict) or not all(
                k in rel for k in ["from_abstraction", "to_abstraction", "label"]
            ):
                raise ValueError(
                    f"Missing keys (expected from_abstraction, to_abstraction, label) in relationship item: {rel}"
                )
            # Validate 'label' is a string
            if not isinstance(rel["label"], str):
                raise ValueError(f"Relationship label is not a string: {rel}")

            # Validate indices
            try:
                from_idx = int(str(rel["from_abstraction"]).split("#")[0].strip())
                to_idx = int(str(rel["to_abstraction"]).split("#")[0].strip())
                if not (
                    0 <= from_idx < num_abstractions and 0 <= to_idx < num_abstractions
                ):
                    raise ValueError(
                        f"Invalid index in relationship: from={from_idx}, to={to_idx}. Max index is {num_abstractions-1}."
                    )
                validated_relationships.append(
                    {
                        "from": from_idx,
                        "to": to_idx,
                        "label": rel["label"],  # Potentially translated label
                    }
                )
            except (ValueError, TypeError):
                raise ValueError(f"Could not parse indices from relationship: {rel}")

        print("Generated project summary and relationship details.")
        return {
            "summary": relationships_data["summary"],  # Potentially translated summary
            "details": validated_relationships,  # Store validated, index-based relationships with potentially translated labels
        }

    def post(self, shared, prep_res, exec_res):
        # Structure is now {"summary": str, "details": [{"from": int, "to": int, "label": str}]}
        # Summary and label might be translated
        from utils.relationships_check import relationship_coverage

        shared["relationships"] = exec_res
        coverage = relationship_coverage(len(shared.get("abstractions") or []), exec_res.get("details") or [])
        shared["relationship_warnings"] = coverage
        if coverage["orphans"]:
            print(
                f"QUICK_STUDY_WARN: relationship orphans={coverage['orphans']} "
                f"edges={coverage['edge_count']}"
            )


class OrderChapters(Node):
    def prep(self, shared):
        abstractions = shared["abstractions"]  # Name/description might be translated
        relationships = shared["relationships"]  # Summary/label might be translated
        project_name = shared["project_name"]  # Get project name
        language = shared.get("language", "english")  # Get language
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True

        # Prepare context for the LLM
        abstraction_info_for_prompt = []
        for i, a in enumerate(abstractions):
            abstraction_info_for_prompt.append(
                f"- {i} # {a['name']}"
            )  # Use potentially translated name
        abstraction_listing = "\n".join(abstraction_info_for_prompt)

        # Use potentially translated summary and labels
        summary_note = ""
        if language.lower() != "english":
            summary_note = (
                f" (Note: Project Summary might be in {language.capitalize()})"
            )

        context = f"Project Summary{summary_note}:\n{relationships['summary']}\n\n"
        context += "Relationships (Indices refer to abstractions above):\n"
        for rel in relationships["details"]:
            from_name = abstractions[rel["from"]]["name"]
            to_name = abstractions[rel["to"]]["name"]
            # Use potentially translated 'label'
            context += f"- From {rel['from']} ({from_name}) to {rel['to']} ({to_name}): {rel['label']}\n"  # Label might be translated

        list_lang_note = ""
        if language.lower() != "english":
            list_lang_note = f" (Names might be in {language.capitalize()})"

        self._pagerank = bool(shared.get("pagerank_order"))
        self._edges = (relationships or {}).get("details") or []
        return (
            abstraction_listing,
            context,
            len(abstractions),
            project_name,
            list_lang_note,
            use_cache,
        )  # Return use_cache

    def exec(self, prep_res):
        (
            abstraction_listing,
            context,
            num_abstractions,
            project_name,
            list_lang_note,
            use_cache,
        ) = prep_res  # Unpack use_cache
        emit_step("order")
        if getattr(self, "_pagerank", False):
            from utils.pagerank_order import pagerank_order

            print("Determining chapter order using in-degree/PageRank...")
            return pagerank_order(num_abstractions, getattr(self, "_edges", []) or [])
        print("Determining chapter order using LLM...")
        # No language variation needed here in prompt instructions, just ordering based on structure
        # The input names might be translated, hence the note.
        prompt = f"""
Given the following project abstractions and their relationships for the project ```` {project_name} ````:

Abstractions (Index # Name){list_lang_note}:
{abstraction_listing}

Context about relationships and project summary:
{context}

If you are going to make a tutorial for ```` {project_name} ````, what is the best order to explain these abstractions, from first to last?
Ideally, first explain those that are the most important or foundational, perhaps user-facing concepts or entry points. Then move to more detailed, lower-level implementation details or supporting concepts.

Output the ordered list of abstraction indices, including the name in a comment for clarity. Use the format `idx # AbstractionName`.

```yaml
- 2 # FoundationalConcept
- 0 # CoreClassA
- 1 # CoreClassB (uses CoreClassA)
- ...
```

Now, provide the YAML output:
"""
        response = call_llm(
            prompt,
            use_cache=(use_cache and self.cur_retry == 0),
            temperature=YAML_TEMPERATURE,
            stage="order",
        )

        ordered_indices_raw = parse_llm_yaml(response)

        if not isinstance(ordered_indices_raw, list):
            raise ValueError("LLM output is not a list")

        ordered_indices = []
        seen_indices = set()
        for entry in ordered_indices_raw:
            try:
                if isinstance(entry, int):
                    idx = entry
                elif isinstance(entry, str) and "#" in entry:
                    idx = int(entry.split("#")[0].strip())
                else:
                    idx = int(str(entry).strip())

                if not (0 <= idx < num_abstractions):
                    raise ValueError(
                        f"Invalid index {idx} in ordered list. Max index is {num_abstractions-1}."
                    )
                if idx in seen_indices:
                    raise ValueError(f"Duplicate index {idx} found in ordered list.")
                ordered_indices.append(idx)
                seen_indices.add(idx)

            except (ValueError, TypeError):
                raise ValueError(
                    f"Could not parse index from ordered list entry: {entry}"
                )

        # Check if all abstractions are included
        if len(ordered_indices) != num_abstractions:
            raise ValueError(
                f"Ordered list length ({len(ordered_indices)}) does not match number of abstractions ({num_abstractions}). Missing indices: {set(range(num_abstractions)) - seen_indices}"
            )

        print(f"Determined chapter order (indices): {ordered_indices}")
        return ordered_indices  # Return the list of indices

    def post(self, shared, prep_res, exec_res):
        # exec_res is already the list of ordered indices
        shared["chapter_order"] = exec_res  # List of indices


class WriteChapters(AsyncParallelBatchNode):
    async def prep_async(self, shared):
        chapter_order = shared["chapter_order"]  # List of indices
        abstractions = shared[
            "abstractions"
        ]  # List of {"name": str, "description": str, "files": [int]}
        files_data = shared["files"]  # List of (path, content) tuples
        project_name = shared["project_name"]
        language = shared.get("language", "english")
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True
        relationships = (shared.get("relationships") or {}).get("details") or []
        overview_only = bool(shared.get("overview_only"))
        resume = bool(shared.get("resume") or shared.get("incremental"))
        map_mode = bool(shared.get("map_mode"))
        output_folder = None
        if shared.get("output_dir") and shared.get("project_name"):
            from utils.resume import tutorial_dir

            output_folder = tutorial_dir(shared["output_dir"], shared["project_name"])

        emit_step("write")
        from utils.mem_guard import apply_concurrency

        conc = apply_concurrency(len(files_data or []))
        self._semaphore = asyncio.Semaphore(conc)
        self._attempts = {}

        # Create a complete list of all chapters
        all_chapters = []
        chapter_outline = []  # Same list plus descriptions, used as cross-chapter context
        chapter_filenames = {}  # Store chapter filename mapping for linking
        for i, abstraction_index in enumerate(chapter_order):
            if 0 <= abstraction_index < len(abstractions):
                chapter_num = i + 1
                chapter_name = abstractions[abstraction_index][
                    "name"
                ]  # Potentially translated name
                # Create safe filename (from potentially translated name)
                safe_name = "".join(
                    c if c.isalnum() else "_" for c in chapter_name
                ).lower()
                filename = f"{i+1:02d}_{safe_name}.md"
                # Format with link (using potentially translated name)
                all_chapters.append(f"{chapter_num}. [{chapter_name}]({filename})")
                chapter_outline.append(
                    f"{chapter_num}. [{chapter_name}]({filename})\n"
                    f"   {abstractions[abstraction_index]['description'].strip()}"
                )
                # Store mapping of chapter index to filename for linking
                chapter_filenames[abstraction_index] = {
                    "num": chapter_num,
                    "name": chapter_name,
                    "filename": filename,
                }

        # Create a formatted string with all chapters
        full_chapter_listing = "\n".join(all_chapters)
        full_chapter_outline = "\n".join(chapter_outline)

        def relationship_edges_for(abstraction_index: int) -> str:
            lines = []
            here = chapter_filenames.get(abstraction_index)
            here_name = here["name"] if here else str(abstraction_index)
            for rel in relationships:
                if rel.get("from") == abstraction_index:
                    other = chapter_filenames.get(rel.get("to"))
                    if other:
                        lines.append(
                            f'- "{here_name}" --{rel.get("label", "")}--> '
                            f'[{other["name"]}]({other["filename"]})'
                        )
                elif rel.get("to") == abstraction_index:
                    other = chapter_filenames.get(rel.get("from"))
                    if other:
                        lines.append(
                            f'- [{other["name"]}]({other["filename"]}) '
                            f'--{rel.get("label", "")}--> "{here_name}"'
                        )
            return "\n".join(lines) if lines else "No relationship edges for this chapter."

        items_to_process = []
        for i, abstraction_index in enumerate(chapter_order):
            if 0 <= abstraction_index < len(abstractions):
                abstraction_details = abstractions[
                    abstraction_index
                ]  # Contains potentially translated name/desc
                # Use 'files' (list of indices) directly
                related_file_indices = abstraction_details.get("files", [])
                # Get content using helper, passing indices
                related_files_content_map = get_content_for_indices(
                    files_data, related_file_indices
                )

                # Get previous chapter info for transitions (uses potentially translated name)
                prev_chapter = None
                if i > 0:
                    prev_idx = chapter_order[i - 1]
                    prev_chapter = chapter_filenames[prev_idx]

                # Get next chapter info for transitions (uses potentially translated name)
                next_chapter = None
                if i < len(chapter_order) - 1:
                    next_idx = chapter_order[i + 1]
                    next_chapter = chapter_filenames[next_idx]

                items_to_process.append(
                    {
                        "chapter_num": i + 1,
                        "abstraction_index": abstraction_index,
                        "abstraction_details": abstraction_details,  # Has potentially translated name/desc
                        "related_files_content_map": related_files_content_map,
                        "project_name": shared["project_name"],  # Add project name
                        "full_chapter_listing": full_chapter_listing,  # Add the full chapter listing (uses potentially translated names)
                        "full_chapter_outline": full_chapter_outline,  # Outline + descriptions, replaces the previous-chapters text
                        "chapter_filenames": chapter_filenames,  # Add chapter filenames mapping (uses potentially translated names)
                        "prev_chapter": prev_chapter,  # Add previous chapter info (uses potentially translated name)
                        "next_chapter": next_chapter,  # Add next chapter info (uses potentially translated name)
                        "language": language,  # Add language for multi-language support
                        "use_cache": use_cache, # Pass use_cache flag
                        "relationship_edges": relationship_edges_for(abstraction_index),
                        "filename": chapter_filenames[abstraction_index]["filename"],
                        "overview_only": overview_only,
                        "map_mode": map_mode,
                        "bilingual": bool(shared.get("bilingual")),
                        "output_folder": str(output_folder) if output_folder else None,
                        "saved_chapter": None,
                    }
                )
                if resume and output_folder:
                    from utils.resume import load_saved_chapter

                    items_to_process[-1]["saved_chapter"] = load_saved_chapter(
                        output_folder, chapter_filenames[abstraction_index]["filename"]
                    )
            else:
                print(
                    f"Warning: Invalid abstraction index {abstraction_index} in chapter_order. Skipping."
                )

        print(
            f"Preparing to write {len(items_to_process)} chapters "
            f"({CHAPTER_CONCURRENCY} at a time)..."
        )
        return items_to_process  # Iterable for AsyncParallelBatchNode

    async def exec_async(self, item):
        # This runs for each item prepared above, concurrently with the other chapters
        abstraction_name = item["abstraction_details"][
            "name"
        ]  # Potentially translated name
        abstraction_description = item["abstraction_details"][
            "description"
        ]  # Potentially translated description
        chapter_num = item["chapter_num"]
        project_name = item.get("project_name")
        language = item.get("language", "english")
        use_cache = item.get("use_cache", True) # Read use_cache from item

        if item.get("output_folder"):
            from utils.job_control import wait_if_paused

            wait_if_paused(Path(item["output_folder"]).parent)
        if item.get("saved_chapter"):
            print(f"Resuming chapter {chapter_num} from disk.")
            return item["saved_chapter"]
        if item.get("overview_only"):
            stub = (
                f"# Chapter {chapter_num}: {abstraction_name}\n\n"
                f"{abstraction_description}\n\n"
                f"{item.get('relationship_edges') or ''}\n\n"
                "（轻量总览模式：本章未展开长文。）\n"
            )
            text = finalize_chapter(stub + ("概述。" * 30), chapter_num, abstraction_name)
            from utils.relationships_check import append_required_links

            text = append_required_links(text, item.get("relationship_edges") or "")
            if item.get("output_folder") and item.get("filename"):
                from utils.resume import save_chapter

                save_chapter(item["output_folder"], item["filename"], text)
            return inject_truncation_note(text, {"truncated_files": 0, "file_count": 0})
        if item.get("map_mode"):
            from utils.map_slices import clip_map_mode_snippets

            file_context_str = clip_map_mode_snippets(item["related_files_content_map"])
            clip_stats = {
                "truncated_files": 0,
                "file_count": len(item.get("related_files_content_map") or {}),
            }
        else:
            file_context_str, clip_stats = clip_snippets_with_stats(item["related_files_content_map"])

        # Add language instruction and context notes only if not English
        language_instruction = ""
        concept_details_note = ""
        structure_note = ""
        outline_note = ""
        instruction_lang_note = ""
        mermaid_lang_note = ""
        code_comment_note = ""
        link_lang_note = ""
        tone_note = ""
        if language.lower() != "english":
            lang_cap = language.capitalize()
            language_instruction = f"IMPORTANT: Write this ENTIRE tutorial chapter in **{lang_cap}**. Some input context (like concept name, description, chapter list, previous summary) might already be in {lang_cap}, but you MUST translate ALL other generated content including explanations, examples, technical terms, and potentially code comments into {lang_cap}. DO NOT use English anywhere except in code syntax, required proper nouns, or when specified. The entire output MUST be in {lang_cap}.\n\n"
            concept_details_note = f" (Note: Provided in {lang_cap})"
            structure_note = f" (Note: Chapter names might be in {lang_cap})"
            outline_note = f" (Note: This outline might be in {lang_cap})"
            instruction_lang_note = f" (in {lang_cap})"
            mermaid_lang_note = f" (Use {lang_cap} for labels/text if appropriate)"
            code_comment_note = f" (Translate to {lang_cap} if possible, otherwise keep minimal English for clarity)"
            link_lang_note = (
                f" (Use the {lang_cap} chapter title from the structure above)"
            )
            tone_note = f" (appropriate for {lang_cap} readers)"
        bilingual_note = ""
        if item.get("bilingual"):
            from utils.bilingual import bilingual_note as _bn

            bilingual_note = _bn(language)

        prompt = f"""
{language_instruction}Write a very beginner-friendly tutorial chapter (in Markdown format) for the project `{project_name}` about the concept: "{abstraction_name}". This is Chapter {chapter_num}.

Concept Details{concept_details_note}:
- Name: {abstraction_name}
- Description:
{abstraction_description}

Complete Tutorial Structure{structure_note}:
{item["full_chapter_listing"]}

What every chapter of this tutorial covers{outline_note}. Chapters are written independently,
so do NOT assume wording from another chapter; when a topic belongs to another chapter, link to
it instead of explaining it again:
{item["full_chapter_outline"]}

Relationship edges for this chapter (stay parallel — do not wait for other chapter bodies;
use these Markdown links when mentioning related abstractions):
{item.get("relationship_edges") or "No relationship edges for this chapter."}

Relevant Code Snippets (Code itself remains unchanged):
{file_context_str if file_context_str else "No specific code snippets provided for this abstraction."}

Instructions for the chapter (Generate content in {language.capitalize()} unless specified otherwise):
- Start with a clear heading (e.g., `# Chapter {chapter_num}: {abstraction_name}`). Use the provided concept name.

- If this is not the first chapter, begin with a brief transition from the previous chapter{instruction_lang_note}, referencing it with a proper Markdown link using its name{link_lang_note}.

- Begin with a high-level motivation explaining what problem this abstraction solves{instruction_lang_note}. Start with a central use case as a concrete example. The whole chapter should guide the reader to understand how to solve this use case. Make it very minimal and friendly to beginners.

- If the abstraction is complex, break it down into key concepts. Explain each concept one-by-one in a very beginner-friendly way{instruction_lang_note}.

- Explain how to use this abstraction to solve the use case{instruction_lang_note}. Give example inputs and outputs for code snippets (if the output isn't values, describe at a high level what will happen{instruction_lang_note}).

- Each code block should be BELOW 10 lines! If longer code blocks are needed, break them down into smaller pieces and walk through them one-by-one. Aggresively simplify the code to make it minimal. Use comments{code_comment_note} to skip non-important implementation details. Each code block MUST include the source file path (as a Markdown italic line immediately above the fence, e.g. `*source: path/to/file.py*`, or as the first comment). Do not invent paths — use the `--- File:` / `# source:` labels from the snippets. Each code block should have a beginner friendly explanation right after it{instruction_lang_note}.

- Describe the internal implementation to help understand what's under the hood{instruction_lang_note}. First provide a non-code or code-light walkthrough on what happens step-by-step when the abstraction is called{instruction_lang_note}. It's recommended to use a simple sequenceDiagram with a dummy example - keep it minimal with at most 5 participants to ensure clarity. If participant name has space, use: `participant QP as Query Processing`. {mermaid_lang_note}.

- Then dive deeper into code for the internal implementation with references to files. Provide example code blocks, but make them similarly simple and beginner-friendly. Explain{instruction_lang_note}.

- IMPORTANT: When you need to refer to other core abstractions covered in other chapters, ALWAYS use proper Markdown links like this: [Chapter Title](filename.md). Use the Complete Tutorial Structure above to find the correct filename and the chapter title{link_lang_note}. Translate the surrounding text.

- Use mermaid diagrams to illustrate complex concepts (```mermaid``` format). {mermaid_lang_note}.

- Heavily use analogies and examples throughout{instruction_lang_note} to help beginners understand.

- End the chapter with a brief conclusion that summarizes what was learned{instruction_lang_note} and provides a transition to the next chapter{instruction_lang_note}. If there is a next chapter, use a proper Markdown link: [Next Chapter Title](next_chapter_filename){link_lang_note}.

- Ensure the tone is welcoming and easy for a newcomer to understand{tone_note}.

- Output *only* the Markdown content for this chapter.
{bilingual_note}

Now, directly provide a super beginner-friendly Markdown output (DON'T need ```markdown``` tags):
"""
        attempt = self._attempts.get(chapter_num, 0)
        self._attempts[chapter_num] = attempt + 1
        use_cache_now = bool(use_cache) and attempt == 0

        from utils.rpm_limit import chapter_limiter

        limiter = getattr(self, "_rpm", None)
        if limiter is None:
            limiter = chapter_limiter()
            self._rpm = limiter
        async with self._semaphore:
            print(f"Writing chapter {chapter_num} for: {abstraction_name} using LLM...")
            def _write_call():
                limiter.acquire()
                try:
                    try:
                        return call_llm(
                            prompt,
                            use_cache_now,
                            f"ch {chapter_num}/{len(item['chapter_filenames'])}",
                            stage="write",
                        )
                    except TypeError:
                        return call_llm(
                            prompt,
                            use_cache_now,
                            f"ch {chapter_num}/{len(item['chapter_filenames'])}",
                        )
                finally:
                    limiter.release()

            chapter_content = await asyncio.to_thread(_write_call)

        text = finalize_chapter(chapter_content, chapter_num, abstraction_name)
        from utils.relationships_check import append_required_links

        text = append_required_links(text, item.get("relationship_edges") or "")
        text = inject_truncation_note(text, clip_stats)
        if item.get("output_folder") and item.get("filename"):
            from utils.resume import save_chapter

            save_chapter(item["output_folder"], item["filename"], text)
        return text

    async def post_async(self, shared, prep_res, exec_res_list):
        # asyncio.gather preserves input order, so exec_res_list still follows chapter_order
        chapters = list(exec_res_list)
        if shared.get("polish"):
            from utils.polish import polish_transitions

            chapters = polish_transitions(chapters, shared.get("chapter_order") or [])
        shared["chapters"] = chapters
        print(f"Finished writing {len(chapters)} chapters.")


class CombineTutorial(Node):
    def prep(self, shared):
        project_name = shared["project_name"]
        output_base_dir = shared.get("output_dir", "output")  # Default output dir
        output_path = os.path.join(output_base_dir, project_name)
        repo_url = shared.get("repo_url")  # Get the repository URL
        # language = shared.get("language", "english") # No longer needed for fixed strings

        # Get potentially translated data
        relationships_data = shared[
            "relationships"
        ]  # {"summary": str, "details": [{"from": int, "to": int, "label": str}]} -> summary/label potentially translated
        chapter_order = shared["chapter_order"]  # indices
        abstractions = shared[
            "abstractions"
        ]  # list of dicts -> name/description potentially translated
        chapters_content = shared[
            "chapters"
        ]  # list of strings -> content potentially translated

        # --- Generate Mermaid Diagram ---
        mermaid_lines = ["flowchart TD"]
        # Add nodes for each abstraction using potentially translated names
        for i, abstr in enumerate(abstractions):
            node_id = f"A{i}"
            # Use potentially translated name, sanitize for Mermaid ID and label
            sanitized_name = abstr["name"].replace('"', "")
            node_label = sanitized_name  # Using sanitized name only
            mermaid_lines.append(
                f'    {node_id}["{node_label}"]'
            )  # Node label uses potentially translated name
        # Add edges for relationships using potentially translated labels
        for rel in relationships_data["details"]:
            from_node_id = f"A{rel['from']}"
            to_node_id = f"A{rel['to']}"
            # Use potentially translated label, sanitize
            edge_label = (
                rel["label"].replace('"', "").replace("\n", " ")
            )  # Basic sanitization
            max_label_len = 30
            if len(edge_label) > max_label_len:
                edge_label = edge_label[: max_label_len - 3] + "..."
            mermaid_lines.append(
                f'    {from_node_id} -- "{edge_label}" --> {to_node_id}'
            )  # Edge label uses potentially translated label

        # --- Prepare index.md content ---
        index_content = f"# Tutorial: {project_name}\n\n"
        index_content += f"{relationships_data['summary']}\n\n"  # Use the potentially translated summary directly
        # Keep fixed strings in English
        index_content += f"**Source Repository:** [{repo_url}]({repo_url})\n\n"
        file_count = shared.get("file_count")
        map_mode = shared.get("map_mode")
        if file_count is not None:
            index_content += (
                f"**Crawl:** {file_count} files"
                f", map_mode={'true' if map_mode else 'false'}\n\n"
            )

        # Keep fixed strings in English
        index_content += f"## 推荐阅读路径\n\n"

        chapter_files = []
        click_lines = []
        # Generate chapter links based on the determined order, using potentially translated names
        for i, abstraction_index in enumerate(chapter_order):
            # Ensure index is valid and we have content for it
            if 0 <= abstraction_index < len(abstractions) and i < len(chapters_content):
                abstraction_name = abstractions[abstraction_index][
                    "name"
                ]  # Potentially translated name
                rationale = (abstractions[abstraction_index].get("description") or "").strip().split("\n")[0][:160]
                # Sanitize potentially translated name for filename
                safe_name = "".join(
                    c if c.isalnum() else "_" for c in abstraction_name
                ).lower()
                filename = f"{i+1:02d}_{safe_name}.md"
                click_lines.append(f'    click A{abstraction_index} "{filename}"')
                index_content += f"{i+1}. [{abstraction_name}]({filename})"
                if rationale:
                    index_content += f" — {rationale}"
                index_content += "\n"

                # Add attribution to chapter content (using English fixed string)
                chapter_content = chapters_content[i]  # Potentially translated content
                if not chapter_content.endswith("\n\n"):
                    chapter_content += "\n\n"
                # Keep fixed strings in English
                chapter_content += f"---\n\nGenerated by [AI Codebase Knowledge Builder](https://github.com/The-Pocket/Tutorial-Codebase-Knowledge)"

                # Store filename and corresponding content
                chapter_files.append({"filename": filename, "content": chapter_content})
            else:
                print(
                    f"Warning: Mismatch between chapter order, abstractions, or content at index {i} (abstraction index {abstraction_index}). Skipping file generation for this entry."
                )

        mermaid_diagram = "\n".join(mermaid_lines + click_lines)
        try:
            from utils.graph_color import color_mermaid

            file_names = [item[0] if isinstance(item, (list, tuple)) else str(item) for item in (shared.get("files") or [])]
            mermaid_diagram = color_mermaid(mermaid_diagram, file_names, by="lang")
        except Exception:
            pass
        # Insert the diagram above the chapter list.
        diagram_block = "```mermaid\n" + mermaid_diagram + "\n```\n\n"
        marker = "## 推荐阅读路径\n\n"
        if marker in index_content:
            index_content = index_content.replace(marker, diagram_block + marker, 1)
        else:
            index_content = diagram_block + index_content

        # Add attribution to index content (using English fixed string)
        index_content += f"\n\n---\n\nGenerated by [AI Codebase Knowledge Builder](https://github.com/The-Pocket/Tutorial-Codebase-Knowledge)"

        return {
            "output_path": output_path,
            "index_content": index_content,
            "chapter_files": chapter_files,  # List of {"filename": str, "content": str}
            "language": shared.get("language", "english"),
            "project_name": project_name,
            "file_count": file_count,
            "repo_url": repo_url,
            "include_patterns": sorted(shared.get("include_patterns") or []),
            "exclude_patterns": sorted(shared.get("exclude_patterns") or []),
            "max_file_size": shared.get("max_file_size"),
            "relationship_warnings": shared.get("relationship_warnings"),
            "abstractions": shared.get("abstractions") or [],
            "files": shared.get("files") or [],
            "relationships": shared.get("relationships") or {},
            "strategy": shared.get("strategy"),
            "local_dir": shared.get("local_dir"),
            "upstream_commit": _upstream_commit(shared.get("local_dir")),
        }

    def exec(self, prep_res):
        output_path = prep_res["output_path"]
        index_content = prep_res["index_content"]
        chapter_files = prep_res["chapter_files"]

        emit_step("combine")
        print(f"Combining tutorial into directory: {output_path}")
        # Rely on Node's built-in retry/fallback
        os.makedirs(output_path, exist_ok=True)

        # Write index.md
        index_filepath = os.path.join(output_path, "index.md")
        with open(index_filepath, "w", encoding="utf-8") as f:
            f.write(index_content)
        print(f"  - Wrote {index_filepath}")

        # Write chapter files
        for chapter_info in chapter_files:
            chapter_filepath = os.path.join(output_path, chapter_info["filename"])
            with open(chapter_filepath, "w", encoding="utf-8") as f:
                f.write(chapter_info["content"])
            print(f"  - Wrote {chapter_filepath}")

        import hashlib
        from utils.dead_links import find_dead_markdown_links

        include_material = json.dumps(
            {
                "include": prep_res.get("include_patterns") or [],
                "exclude": prep_res.get("exclude_patterns") or [],
                "max_size": prep_res.get("max_file_size"),
            },
            sort_keys=True,
        )
        dead = find_dead_markdown_links(output_path)
        if dead:
            print(f"QUICK_STUDY_WARN: dead_links={len(dead)}")
        meta = {
            "name": prep_res.get("project_name"),
            "language": prep_res.get("language") or "english",
            "file_count": prep_res.get("file_count"),
            "repo_url": prep_res.get("repo_url"),
            "include_hash": hashlib.sha256(include_material.encode("utf-8")).hexdigest()[:12],
            "dead_links": dead,
            "relationship_warnings": prep_res.get("relationship_warnings"),
            "chapter_count": len(chapter_files),
            "strategy": prep_res.get("strategy"),
            "local_dir": prep_res.get("local_dir"),
            "upstream_commit": prep_res.get("upstream_commit"),
        }
        meta_path = os.path.join(output_path, "meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"  - Wrote {meta_path}")

        from pathlib import Path as _P
        from utils.abstraction_map import write_abstraction_map
        from utils.glossary import build_glossary
        from utils.heatmap import heatmap_markdown
        from utils.timeout_hint import job_timeout_hint
        from utils.versions import snapshot_tutorial

        write_abstraction_map(
            _P(output_path),
            prep_res.get("abstractions") or [],
            prep_res.get("files") or [],
        )
        names = [item.get("name") or "" for item in (prep_res.get("abstractions") or [])]
        edges = ((prep_res.get("relationships") or {}).get("details") or [])
        (_P(output_path) / "glossary.md").write_text(build_glossary(_P(output_path)), encoding="utf-8")
        (_P(output_path) / "heatmap.md").write_text(heatmap_markdown(names, edges), encoding="utf-8")
        stamp = snapshot_tutorial(_P(output_path))
        print(f"  - version snapshot {stamp}")
        hint = job_timeout_hint(len(chapter_files))
        print(f"QUICK_STUDY_TIMEOUT_HINT: {hint['hint']}")

        return output_path  # Return the final path

    def post(self, shared, prep_res, exec_res):
        shared["final_output_dir"] = exec_res  # Store the output path
        print(f"\nTutorial generation complete! Files are in: {exec_res}")
