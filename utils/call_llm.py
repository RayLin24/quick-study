import hashlib
import os
import logging
import json
import threading
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configure logging
log_directory = os.getenv("LOG_DIR", "logs")
os.makedirs(log_directory, exist_ok=True)
log_file = os.path.join(
    log_directory, f"llm_calls_{datetime.now().strftime('%Y%m%d')}.log"
)

# Set up logger
logger = logging.getLogger("llm_logger")
logger.setLevel(logging.INFO)
logger.propagate = False  # Prevent propagation to root logger
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)

# One file per prompt instead of a single JSON blob: a run rewrote the whole cache
# on every call, and the keys are full prompts, so the blob grew by megabytes per run.
cache_dir = os.getenv("LLM_CACHE_DIR", "llm_cache")
legacy_cache_file = "llm_cache.json"

# No timeout means a stalled connection hangs the whole run forever. With streaming the
# read timeout measures the gap between chunks, so it detects a stall without capping
# how long a long chapter may take to finish.
request_timeout = (10, float(os.getenv("LLM_TIMEOUT_SECONDS", "180")))
stream_enabled = os.getenv("LLM_STREAM", "1").lower() not in ("0", "false", "no")
progress_interval = float(os.getenv("LLM_PROGRESS_SECONDS", "5"))

_print_lock = threading.Lock()
_legacy_cache = None
_legacy_loaded = False
STREAM_FALLBACK_STATUSES = {400, 404, 415, 422}


class EmptyLLMResponse(Exception):
    """The provider returned HTTP 200 but no usable text."""


def _prompt_log_text(prompt: str, limit: int = 4000) -> str:
    if len(prompt) <= limit:
        return f"PROMPT: {prompt}"
    return f"PROMPT ({len(prompt)} chars): {prompt[:limit]}..."


def _provider_error_text(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if err is None:
        return None
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        message = err.get("message") or err.get("msg") or err.get("code")
        if message:
            return str(message)
        return str(err)
    return str(err)


def _cache_path(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{digest}.json")


def _load_legacy_cache():
    global _legacy_cache, _legacy_loaded
    if _legacy_loaded:
        return _legacy_cache or {}
    _legacy_loaded = True
    try:
        with open(legacy_cache_file, 'r', encoding='utf-8') as f:
            _legacy_cache = json.load(f)
    except FileNotFoundError:
        _legacy_cache = {}
    except Exception:
        logger.warning("Failed to load legacy cache.")
        _legacy_cache = {}
    return _legacy_cache


def load_cache(prompt: str):
    """Return the cached response for one prompt, or None."""
    try:
        with open(_cache_path(prompt), 'r', encoding='utf-8') as f:
            return json.load(f)["response"]
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("Failed to read cache entry.")
    return _load_legacy_cache().get(prompt)


def save_cache(prompt: str, response: str) -> None:
    if not (response or "").strip():
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = _cache_path(prompt)
        # Write then rename so parallel chapter jobs never read a half-written entry.
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({"prompt": prompt, "response": response}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.warning("Failed to save cache")


def get_llm_provider():
    provider = os.getenv("LLM_PROVIDER")
    if not provider and (os.getenv("GEMINI_PROJECT_ID") or os.getenv("GEMINI_API_KEY")):
        provider = "GEMINI"
    # if necessary, add ANTHROPIC/OPENAI
    return provider


def _emit(line: str) -> None:
    with _print_lock:
        print(line, flush=True)


class _Progress:
    """Throttled one-line-per-update progress reporter, safe for parallel callers."""

    def __init__(self, label):
        self.label = label
        self.chars = 0
        self.thinking = 0
        self.started = time.monotonic()
        self.last = self.started

    def add(self, n: int, thinking: bool = False) -> None:
        if thinking:
            self.thinking += n
        else:
            self.chars += n
        if not self.label:
            return
        now = time.monotonic()
        if now - self.last < progress_interval:
            return
        self.last = now
        # Reasoning models emit nothing but reasoning for the first minute; without this
        # the job looks hung.
        state = f"{self.chars:,} chars" if self.chars else f"thinking ({self.thinking:,} chars)"
        _emit(f"  [{self.label}] {state}, {now - self.started:.0f}s...")

    def done(self) -> None:
        # Non-streaming providers never report deltas, so there is nothing to summarise.
        if self.label and (self.chars or self.thinking):
            _emit(
                f"  [{self.label}] done: {self.chars:,} chars in "
                f"{time.monotonic() - self.started:.0f}s"
            )


def _decode_sse_line(raw) -> str:
    """SSE bodies are UTF-8. Gateways often omit charset, and requests then
    treats the stream as Latin-1, which turns Chinese into C1 controls."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    text = raw
    if any(0x80 <= ord(ch) <= 0x9F for ch in text):
        try:
            return text.encode("latin-1").decode("utf-8")
        except UnicodeError:
            return text
    return text


def _stream_chat_completion(url, headers, payload, progress: _Progress) -> str:
    payload = {**payload, "stream": True}
    with requests.post(
        url, headers=headers, json=payload, timeout=request_timeout, stream=True
    ) as response:
        if response.status_code != 200:
            raise Exception(
                f"HTTP error occurred: {response.status_code} (Details: {response.text[:500]})"
            )
        response.encoding = "utf-8"
        parts = []
        for raw in response.iter_lines(decode_unicode=False):
            line = _decode_sse_line(raw)
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            error_text = _provider_error_text(chunk)
            if error_text:
                raise Exception(f"LLM stream error: {error_text}")
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                parts.append(piece)
                progress.add(len(piece))
            elif delta.get("reasoning_content"):
                progress.add(len(delta["reasoning_content"]), thinking=True)
            else:
                # Some gateways only put the full answer on the last chunk.
                message = (choices[0].get("message") or {}).get("content")
                if message and not parts:
                    parts.append(message)
                    progress.add(len(message))
    text = "".join(parts)
    if not text.strip():
        raise EmptyLLMResponse("Streaming response contained no content")
    return text


def _blocking_chat_completion(url, headers, payload) -> str:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=request_timeout)
        response_json = response.json()
        logger.info("RESPONSE:\n%s", json.dumps(response_json, indent=2))
        response.raise_for_status()
        text = response_json["choices"][0]["message"]["content"] or ""
        if not text.strip():
            raise EmptyLLMResponse("Blocking response contained no content")
        return text
    except requests.exceptions.HTTPError as e:
        error_message = f"HTTP error occurred: {e}"
        try:
            error_details = response.json().get("error", "No additional details")
            error_message += f" (Details: {error_details})"
        except Exception:
            pass
        raise Exception(error_message)


def _call_llm_provider(prompt: str, progress: _Progress) -> str:
    """
    Call an LLM provider based on environment variables.
    Environment variables:
    - LLM_PROVIDER: "OLLAMA" or "XAI"
    - <provider>_MODEL: Model name (e.g., OLLAMA_MODEL, XAI_MODEL)
    - <provider>_BASE_URL: Base URL without endpoint (e.g., OLLAMA_BASE_URL, XAI_BASE_URL)
    - <provider>_API_KEY: API key (e.g., OLLAMA_API_KEY, XAI_API_KEY; optional for providers that don't require it)
    The endpoint /v1/chat/completions will be appended to the base URL.
    """
    # Read the provider from environment variable
    provider = os.environ.get("LLM_PROVIDER")
    if not provider:
        raise ValueError("LLM_PROVIDER environment variable is required")

    # Construct the names of the other environment variables
    model_var = f"{provider}_MODEL"
    base_url_var = f"{provider}_BASE_URL"
    api_key_var = f"{provider}_API_KEY"

    # Read the provider-specific variables
    model = os.environ.get(model_var)
    base_url = os.environ.get(base_url_var)
    api_key = os.environ.get(api_key_var, "")  # API key is optional, default to empty string

    # Validate required variables
    if not model:
        raise ValueError(f"{model_var} environment variable is required")
    if not base_url:
        raise ValueError(f"{base_url_var} environment variable is required")

    # Append the endpoint to the base URL
    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    # Configure headers and payload based on provider
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:  # Only add Authorization header if API key is provided
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }

    try:
        if stream_enabled:
            try:
                return _stream_chat_completion(url, headers, payload, progress)
            except EmptyLLMResponse:
                logger.warning("Streaming response empty; retrying without stream")
                return _blocking_chat_completion(url, headers, payload)
            except Exception as exc:
                status = None
                message = str(exc)
                if message.startswith("HTTP error occurred:"):
                    try:
                        status = int(message.split(":", 1)[1].split()[0])
                    except (IndexError, ValueError):
                        status = None
                if status in STREAM_FALLBACK_STATUSES:
                    logger.warning("Streaming unsupported (%s); retrying without stream", status)
                    return _blocking_chat_completion(url, headers, payload)
                raise
        return _blocking_chat_completion(url, headers, payload)
    except EmptyLLMResponse:
        raise
    except requests.exceptions.ConnectionError:
        raise Exception(f"Failed to connect to {provider} API. Check your network connection.")
    except requests.exceptions.Timeout:
        raise Exception(
            f"Request to {provider} API timed out after {request_timeout[1]:.0f}s "
            f"without data. Raise LLM_TIMEOUT_SECONDS if the model is just slow."
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"An error occurred while making the request to {provider}: {e}")
    except ValueError:
        raise Exception(f"Failed to parse response as JSON from {provider}. The server might have returned an invalid response.")


# By default, we Google Gemini 2.5 pro, as it shows great performance for code understanding
def call_llm(prompt: str, use_cache: bool = True, progress_label: str = None) -> str:
    logger.info(_prompt_log_text(prompt))

    # Check cache if enabled
    if use_cache:
        cached = load_cache(prompt)
        if cached is not None:
            logger.info(f"RESPONSE: {cached}")
            if progress_label:
                _emit(f"  [{progress_label}] cache hit")
            return cached

    progress = _Progress(progress_label)
    provider = get_llm_provider()
    if provider == "GEMINI":
        response_text = _call_llm_gemini(prompt)
    else:  # generic method using a URL that is OpenAI compatible API (Ollama, ...)
        response_text = _call_llm_provider(prompt, progress)
    progress.done()

    # Log the response
    logger.info(f"RESPONSE: {response_text}")

    if not (response_text or "").strip():
        raise EmptyLLMResponse("LLM returned an empty response")

    # Update cache if enabled
    if use_cache:
        save_cache(prompt, response_text)

    return response_text


def _gemini_http_options():
    timeout_ms = int(request_timeout[1] * 1000)
    return {"timeout": timeout_ms}


def _call_llm_gemini(prompt: str) -> str:
    # Imported here so the other providers do not need google-genai installed.
    from google import genai

    http_options = _gemini_http_options()
    client_kwargs = {}
    if os.getenv("GEMINI_PROJECT_ID"):
        client_kwargs = {
            "vertexai": True,
            "project": os.getenv("GEMINI_PROJECT_ID"),
            "location": os.getenv("GEMINI_LOCATION", "us-central1"),
        }
    elif os.getenv("GEMINI_API_KEY"):
        client_kwargs = {"api_key": os.getenv("GEMINI_API_KEY")}
    else:
        raise ValueError("Either GEMINI_PROJECT_ID or GEMINI_API_KEY must be set in the environment")
    try:
        client = genai.Client(**client_kwargs, http_options=http_options)
    except TypeError:
        client = genai.Client(**client_kwargs)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro-exp-03-25")
    response = client.models.generate_content(
        model=model,
        contents=[prompt]
    )
    text = response.text or ""
    if not text.strip():
        raise EmptyLLMResponse("Gemini returned an empty response")
    return text

if __name__ == "__main__":
    test_prompt = "Hello, how are you?"

    # First call - should hit the API
    print("Making call...")
    response1 = call_llm(test_prompt, use_cache=False)
    print(f"Response: {response1}")
