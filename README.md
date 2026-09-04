> 本仓库基于 [PocketFlow-Tutorial-Codebase-Knowledge](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge) 落地。中文说明（项目做什么、如何启动、需要哪些配置）见 [项目说明书.md](./项目说明书.md)。

<h1 align="center">Turns Codebase into Easy Tutorial with AI</h1>

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
 <a href="https://discord.gg/hUHHE9Sa6T">
    <img src="https://img.shields.io/discord/1346833819172601907?logo=discord&style=flat">
</a>
> *Ever stared at a new codebase written by others feeling completely lost? This tutorial shows you how to build an AI agent that analyzes GitHub repositories and creates beginner-friendly tutorials explaining exactly how the code works.*

<p align="center">
  <img
    src="./assets/banner.png" width="800"
  />
</p>

This is a tutorial project of [Pocket Flow](https://github.com/The-Pocket/PocketFlow), a 100-line LLM framework. It crawls GitHub repositories and builds a knowledge base from the code. It analyzes entire codebases to identify core abstractions and how they interact, and transforms complex code into beginner-friendly tutorials with clear visualizations.


- Check out the [book "Crack Any Codebase with AI"](https://www.manning.com/books/crack-any-codebase-with-ai) for more! 
 
- Check out the [YouTube Development Tutorial](https://youtu.be/AFY67zOpbSo) for more!

- Check out the [Substack Post Tutorial](https://zacharyhuang.substack.com/p/ai-codebase-knowledge-builder-full) for more!

&nbsp;&nbsp;**🔸 🎉 Reached Hacker News Front Page** (April 2025) with >900 up‑votes:  [Discussion »](https://news.ycombinator.com/item?id=43739456)



## ⭐ Example Results for Popular GitHub Repositories!

<p align="center">
    <img
      src="./assets/example.png" width="600"
    />
</p>

🤯 All these tutorials are generated **entirely by AI** by crawling the GitHub repo!

- [AutoGen Core](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/AutoGen%20Core) - Build AI teams that talk, think, and solve problems together like coworkers!

- [Browser Use](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Browser%20Use) - Let AI surf the web for you, clicking buttons and filling forms like a digital assistant!

- [Celery](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Celery) - Supercharge your app with background tasks that run while you sleep!

- [Click](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Click) - Turn Python functions into slick command-line tools with just a decorator!

- [Codex](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Codex) - Turn plain English into working code with this AI terminal wizard!

- [Crawl4AI](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Crawl4AI) - Train your AI to extract exactly what matters from any website!

- [CrewAI](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/CrewAI) - Assemble a dream team of AI specialists to tackle impossible problems!

- [DSPy](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/DSPy) - Build LLM apps like Lego blocks that optimize themselves!

- [FastAPI](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/FastAPI) - Create APIs at lightning speed with automatic docs that clients will love!

- [Flask](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Flask) - Craft web apps with minimal code that scales from prototype to production!

- [Google A2A](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Google%20A2A) - The universal language that lets AI agents collaborate across borders!

- [LangGraph](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/LangGraph) - Design AI agents as flowcharts where each step remembers what happened before!

- [LevelDB](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/LevelDB) - Store data at warp speed with Google's engine that powers blockchains!

- [MCP Python SDK](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/MCP%20Python%20SDK) - Build powerful apps that communicate through an elegant protocol without sweating the details!

- [NumPy Core](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/NumPy%20Core) - Master the engine behind data science that makes Python as fast as C!

- [OpenManus](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/OpenManus) - Build AI agents with digital brains that think, learn, and use tools just like humans do!

- [PocketFlow](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/PocketFlow) - 100-line LLM framework. Let Agents build Agents!

- [Pydantic Core](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Pydantic%20Core) - Validate data at rocket speed with just Python type hints!

- [Requests](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/Requests) - Talk to the internet in Python with code so simple it feels like cheating!

- [SmolaAgents](https://the-pocket.github.io/PocketFlow-Tutorial-Codebase-Knowledge/SmolaAgents) - Build tiny AI agents that punch way above their weight class!

- Showcase Your AI-Generated Tutorials in [Discussions](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge/discussions)!

## 🚀 Getting Started

1. Clone this repository
   ```bash
   git clone https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up LLM in [`utils/call_llm.py`](./utils/call_llm.py) by providing credentials. Copy [`.env.sample`](./.env.sample) to `.env`. The default is OpenRouter:
   ```env
   LLM_PROVIDER=OPENROUTER
   OPENROUTER_MODEL=z-ai/glm-5.3-flash
   OPENROUTER_BASE_URL=https://openrouter.ai/api
   OPENROUTER_API_KEY=your_openrouter_key
   LLM_TIMEOUT_SECONDS=300
   ```
   `OPENROUTER_BASE_URL` must **not** include a trailing `/v1`; the client POSTs to `{BASE_URL}/v1/chat/completions` (`https://openrouter.ai/api/v1/chat/completions`). Gemini is optional: set `LLM_PROVIDER=GEMINI` plus `GEMINI_API_KEY` or `GEMINI_PROJECT_ID`. Other OpenAI-compatible providers still use `{PROVIDER}_MODEL` / `{PROVIDER}_BASE_URL` / `{PROVIDER}_API_KEY`. If using Ollama, the url is `http://localhost:11434/` and the API key can be omitted.
   Verify the wiring with:
   ```bash
   python utils/call_llm.py
   ```
   A missing `OPENROUTER_API_KEY` prints a readable `QUICK_STUDY_ERROR:` naming the default model and endpoint.

   Web UI (bind loopback only). Non-loopback (`0.0.0.0`) refuses to start unless `QUICK_STUDY_TOKEN` is set:

   ```bash
   python -m uvicorn webapp:app --host 127.0.0.1 --port 8000
   # or: python -m web.serve --host 127.0.0.1 --port 8000
   ```

   Open http://127.0.0.1:8000 . Set `GITHUB_TOKEN` in `.env` if needed — do not put the token on argv. `LLM_MAX_CONCURRENCY` (default 5), `LLM_CACHE_DIR` (default `llm_cache/`), and `LLM_TIMEOUT_SECONDS` (default 300) are documented in `.env.sample`.

5. Generate a complete codebase tutorial by running the main script:
    ```bash
    # Analyze a GitHub repository
    python main.py --repo https://github.com/username/repo --include "*.py" "*.js" --exclude "tests/*" --max-size 50000

    # Or, analyze a local directory
    python main.py --dir /path/to/your/codebase --include "*.py" --exclude "*test*"

    # Or, generate a tutorial in Chinese
    python main.py --repo https://github.com/username/repo --language "Chinese"
    ```

    - `--repo` or `--dir` - Specify either a GitHub repo URL or a local directory path (required, mutually exclusive)
    - `-n, --name` - Project name (optional, derived from URL/directory if omitted)
    - GitHub token — **environment only** (`GITHUB_TOKEN`). Do not pass `-t` / tokens on argv.
    - `-o, --output` - Output directory (default: ./output)
    - `-i, --include` - Files to include (e.g., "`*.py`" "`*.js`")
    - `-e, --exclude` - Files to exclude (e.g., "`tests/*`" "`docs/*`")
    - `-s, --max-size` - Maximum file size in bytes (default: 100KB)
    - `--language` - Language for the generated tutorial (default: "english")
    - `--max-abstractions` - Maximum number of abstractions to identify (default: 10)
    - `--no-cache` - Disable LLM response caching (default: caching enabled)

The application will crawl the repository, analyze the codebase structure, generate tutorial content in the specified language, and save the output in the specified directory (default: ./output).


<details>
 
<summary> 🐳 <b>Running with Docker</b> </summary>

To run this project in a Docker container, you'll need to pass your API keys as environment variables. 

1. Build the Docker image
   ```bash
   docker build -t pocketflow-app .
   ```

2. Run the container

   You'll need to provide your `OPENROUTER_API_KEY` for the default LLM. If you're analyzing private GitHub repositories or want to avoid rate limits, also provide your `GITHUB_TOKEN`.
   
   Mount a local directory to `/app/output` inside the container to access the generated tutorials on your host machine.
   
   **Example for analyzing a public GitHub repository:**
   
   ```bash
   docker run -it --rm \
     -e OPENROUTER_API_KEY="YOUR_OPENROUTER_API_KEY_HERE" \
     -v "$(pwd)/output_tutorials":/app/output \
     pocketflow-app --repo https://github.com/username/repo
   ```
   
   **Example for analyzing a local directory:**
   
   ```bash
   docker run -it --rm \
     -e OPENROUTER_API_KEY="YOUR_OPENROUTER_API_KEY_HERE" \
     -v "/path/to/your/local_codebase":/app/code_to_analyze \
     -v "$(pwd)/output_tutorials":/app/output \
     pocketflow-app --dir /app/code_to_analyze
   ```
</details>

## 💡 Development Tutorial

- I built using [**Agentic Coding**](https://zacharyhuang.substack.com/p/agentic-coding-the-most-fun-way-to), the fastest development paradigm, where humans simply [design](docs/design.md) and agents [code](flow.py).

- The secret weapon is [Pocket Flow](https://github.com/The-Pocket/PocketFlow), a 100-line LLM framework that lets Agents (e.g., Cursor AI) build for you

- Check out the Step-by-step YouTube development tutorial:

<br>
<div align="center">
  <a href="https://youtu.be/AFY67zOpbSo" target="_blank">
    <img src="./assets/youtube_thumbnail.png" width="500" alt="Pocket Flow Codebase Tutorial" style="cursor: pointer;">
  </a>
</div>
<br>



