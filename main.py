import asyncio
import dotenv
import os
import argparse
import time
# Import the function that creates the flow
from flow import create_tutorial_flow
from utils.language import DEFAULT_LANGUAGE
from utils.patterns import DEFAULT_EXCLUDE_PATTERNS, DEFAULT_INCLUDE_PATTERNS
from utils.strategy import apply_strategy

dotenv.load_dotenv()

# --- Main Function ---
def main():
    parser = argparse.ArgumentParser(description="Generate a tutorial for a GitHub codebase or local directory.")

    # Create mutually exclusive group for source
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--repo", help="URL of the public GitHub repository.")
    source_group.add_argument("--dir", help="Path to local directory.")

    parser.add_argument("-n", "--name", help="Project name (optional, derived from repo/directory if omitted).")
    parser.add_argument(
        "-t",
        "--token",
        help="Deprecated. Use GITHUB_TOKEN in the environment; do not put tokens on argv.",
    )
    parser.add_argument("-o", "--output", default="output", help="Base directory for output (default: ./output).")
    parser.add_argument("-i", "--include", nargs="+", help="Include file patterns (e.g. '*.py' '*.js'). Defaults to common code files if not specified.")
    parser.add_argument("-e", "--exclude", nargs="+", help="Exclude file patterns (e.g. 'tests/*' 'docs/*'). Defaults to test/build directories if not specified.")
    parser.add_argument("-s", "--max-size", type=int, default=100000, help="Maximum file size in bytes (default: 100000, about 100KB).")
    # Add language parameter for multi-language support
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help=f"Language for the generated tutorial (default: {DEFAULT_LANGUAGE})")
    # Add use_cache parameter to control LLM caching
    parser.add_argument("--no-cache", action="store_true", help="Disable LLM response caching (default: caching enabled)")
    # Add max_abstraction_num parameter to control the number of abstractions
    parser.add_argument("--max-abstractions", type=int, default=10, help="Maximum number of abstractions to identify (default: 10)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只爬不写：打印文件数与估 LLM 调用后退出，不生成教程。",
    )
    parser.add_argument("--resume", action="store_true", help="从已落盘章节继续，跳过已完成章。")
    parser.add_argument("--incremental", action="store_true", help="按源文件指纹只重写受影响章。")
    parser.add_argument("--overview-only", action="store_true", help="轻量总览：只出总览与关系，不写长章。")
    parser.add_argument("--polish", action="store_true", help="可选：串行补相邻章过渡（默认关）。")
    parser.add_argument("--strategy", choices=["beginner", "deep", "skim"], help="新手/深度/速览预设。")
    parser.add_argument("--learning-goal", default="", help="学习目标，约束 Identify 选抽象。")
    parser.add_argument("--seed", nargs="+", help="强调阅读的种子文件路径。")
    parser.add_argument("--bilingual", action="store_true", help="章内加另一语言小标题。")
    parser.add_argument("--pagerank-order", action="store_true", help="用入度/PageRank 排章，跳过 LLM 排序。")
    parser.add_argument("--pr-diff", help="PR diff 文件，生成导读提示后退出。")
    parser.add_argument("--issue-title", default="", help="Issue 标题（配合 --issue-body）。")
    parser.add_argument("--issue-body", default="", help="Issue 正文，生成讨论导读后退出。")

    args = parser.parse_args()
    if args.strategy:
        preset = apply_strategy(
            {
                "max_abstractions": None if args.max_abstractions == 10 else args.max_abstractions,
                "max_size": None if args.max_size == 100000 else args.max_size,
                "include": " ".join(args.include) if args.include else "",
                "overview_only": args.overview_only or None,
            },
            args.strategy,
        )
        if not args.include and preset.get("include"):
            args.include = preset["include"].split()
        if args.max_abstractions == 10:
            args.max_abstractions = preset["max_abstractions"]
        if args.max_size == 100000:
            args.max_size = preset["max_size"]
        if preset.get("overview_only"):
            args.overview_only = True

    # GitHub token: environment only. -t is accepted for old scripts but warned.
    github_token = None
    if args.repo:
        if args.token:
            print(
                "Warning: -t/--token puts the secret on argv. "
                "Set GITHUB_TOKEN in the environment instead."
            )
        github_token = os.environ.get("GITHUB_TOKEN") or args.token
        if not github_token:
            print("Warning: No GitHub token provided. You might hit rate limits for public repositories.")

    # Initialize the shared dictionary with inputs
    shared = {
        "repo_url": args.repo,
        "local_dir": args.dir,
        "project_name": args.name, # Can be None, FetchRepo will derive it
        "github_token": github_token,
        "output_dir": args.output, # Base directory for CombineTutorial output

        # Add include/exclude patterns and max file size
        "include_patterns": set(args.include) if args.include else DEFAULT_INCLUDE_PATTERNS,
        "exclude_patterns": set(args.exclude) if args.exclude else DEFAULT_EXCLUDE_PATTERNS,
        "max_file_size": args.max_size,
        "include_specified": bool(args.include),

        # Add language for multi-language support
        "language": args.language,
        "resume": bool(args.resume or args.incremental),
        "incremental": bool(args.incremental),
        "overview_only": bool(args.overview_only),
        "polish": bool(args.polish),
        "strategy": args.strategy,
        "learning_goal": args.learning_goal,
        "seed_files": args.seed,
        "bilingual": bool(args.bilingual),
        "pagerank_order": bool(args.pagerank_order),
        
        # Add use_cache flag (inverse of no-cache flag)
        "use_cache": not args.no_cache,
        
        # Add max_abstraction_num parameter
        "max_abstraction_num": args.max_abstractions,

        # Outputs will be populated by the nodes
        "files": [],
        "abstractions": [],
        "relationships": {},
        "chapter_order": [],
        "chapters": [],
        "final_output_dir": None
    }

    if args.pr_diff:
        from pathlib import Path as _P
        from utils.pr_guide import build_pr_guide_prompt

        print(build_pr_guide_prompt(_P(args.pr_diff).read_text(encoding="utf-8", errors="replace")))
        raise SystemExit(0)
    if args.issue_title or args.issue_body:
        from utils.issue_guide import build_issue_guide_prompt

        print(build_issue_guide_prompt(args.issue_title, args.issue_body))
        raise SystemExit(0)

    if args.dry_run:
        from utils.preview import format_preview_report, preview_generation

        preview = preview_generation(shared)
        print(format_preview_report(preview))
        raise SystemExit(0 if preview.get("ok") else 2)

    # Display starting message with repository/directory and language
    print(f"Starting tutorial generation for: {args.repo or args.dir} in {args.language.capitalize()} language")
    print(f"LLM caching: {'Disabled' if args.no_cache else 'Enabled'}")

    # Create the flow instance
    tutorial_flow = create_tutorial_flow()

    # Run the flow
    started = time.monotonic()
    asyncio.run(tutorial_flow.run_async(shared))
    print(f"Total time: {time.monotonic() - started:.0f}s")
    try:
        from utils.call_llm import usage_meter
        print(usage_meter.format_line())
    except Exception:
        pass

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "ask":
        from utils.ask_cli import main_ask

        raise SystemExit(main_ask(sys.argv[2:]))
    main()
