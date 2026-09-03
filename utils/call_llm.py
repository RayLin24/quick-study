import hashlib
import os
import logging
import json
import threading
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

try:
    from utils.errors import format_error
except ImportError:  # python utils/call_llm.py
    from errors import format_error

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
# OpenRouter + glm-5.3-flash is the product default. Gemini is optional
# (LLM_PROVIDER=GEMINI). Do not join Zhipu /api/paas/v4 into this URL.
DEFAULT_PROVIDER = "OPENROUTER"
DEFAULT_OPENROUTER_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 300

request_timeout = (10, float(os.getenv("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))))
# Default stream off: an empty SSE body plus a blocking retry is a second billed call.
stream_enabled = os.getenv("LLM_STREAM", "0").lower() not in ("0", "false", "no")
stream_fallback_enabled = os.getenv("LLM_STREAM_FALLBACK", "0").lower() not in ("0", "false", "no")
progress_interval = float(os.getenv("LLM_PROGRESS_SECONDS", "5"))
_raw_max_tokens = os.getenv("LLM_MAX_TOKENS", "").strip()
max_tokens = int(_raw_max_tokens) if _raw_max_tokens.isdigit() and int(_raw_max_tokens) > 0 else None

_print_lock = threading.Lock()
_legacy_cache = None
_legacy_loaded = False
STREAM_FALLBACK_STATUSES = {400, 404, 415, 422}


class EmptyLLMResponse(Exception):
    """The provider returned HTTP 200 but no usable text."""


class UsageMeter:
    """Process-wide token totals for the success card / CLI summary."""

    def __init__(self):
        self._lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.calls = 0

    def add(self, usage) -> None:
        if not isinstance(usage, dict):
            return
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        if prompt == 0 and completion == 0 and total == 0:
            return
        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            self.calls += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "calls": self.calls,
                "max_tokens": max_tokens,
            }

    def format_line(self) -> str:
        snap = self.snapshot()
        extra = f" max_tokens={snap['max_tokens']}" if snap["max_tokens"] else ""
        return (
            f"QUICK_STUDY_USAGE: prompt={snap['prompt_tokens']} "
            f"completion={snap['completion_tokens']} total={snap['total_tokens']} "
            f"calls={snap['calls']}{extra}"
        )


usage_meter = UsageMeter()


def parse_usage(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return usage


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


def llm_identity() -> tuple[str, str]:
    provider = get_llm_provider()
    if provider == "OPENROUTER":
        model = _clean_env("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    elif provider == "GEMINI":
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro-exp-03-25")
    else:
        model = _clean_env(f"{provider}_MODEL") or "unknown"
    return provider, model


def _cache_material(prompt: str) -> str:
    provider, model = llm_identity()
    return f"{provider}\n{model}\n{prompt}"


def _cache_path(prompt: str) -> str:
    digest = hashlib.sha256(_cache_material(prompt).encode("utf-8")).hexdigest()
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
    # Legacy blob was prompt-only and leaked answers across models. Ignore it.
    return None


def save_cache(prompt: str, response: str) -> None:
    if not (response or "").strip():
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = _cache_path(prompt)
        # Write then rename so parallel chapter jobs never read a half-written entry.
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            provider, model = llm_identity()
            json.dump(
                {"provider": provider, "model": model, "prompt": prompt, "response": response},
                f,
                ensure_ascii=False,
            )
        os.replace(tmp, path)
    except Exception:
        logger.warning("Failed to save cache")


def _clean_env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    # .env.sample uses <PLACEHOLDER>; treat that as unset.
    if len(value) > 2 and value.startswith("<") and value.endswith(">"):
        return default
    return value


def get_llm_provider() -> str:
    provider = _clean_env("LLM_PROVIDER")
    if not provider:
        return DEFAULT_PROVIDER
    return provider.upper()


def missing_openrouter_key_message() -> str:
    endpoint = f"{DEFAULT_OPENROUTER_BASE_URL.rstrip('/')}{CHAT_COMPLETIONS_PATH}"
    return (
        "OPENROUTER_API_KEY is not set. "
        "Copy .env.sample to .env and add your OpenRouter key. "
        f"Default provider is {DEFAULT_PROVIDER}, model {DEFAULT_OPENROUTER_MODEL}, "
        f"POST {endpoint}. "
        "Gemini is optional: set LLM_PROVIDER=GEMINI and GEMINI_API_KEY."
    )


def chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{CHAT_COMPLETIONS_PATH}"


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


def _apply_max_tokens(payload: dict) -> dict:
    if max_tokens:
        payload = {**payload, "max_tokens": max_tokens}
    return payload


def _stream_chat_completion(url, headers, payload, progress: _Progress) -> str:
    payload = _apply_max_tokens({**payload, "stream": True, "stream_options": {"include_usage": True}})
    with requests.post(
        url, headers=headers, json=payload, timeout=request_timeout, stream=True
    ) as response:
        if response.status_code != 200:
            raise Exception(
                f"HTTP error occurred: {response.status_code} (Details: {response.text[:500]})"
            )
        response.encoding = "utf-8"
        parts = []
        last_usage = None
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
            usage = parse_usage(chunk)
            if usage:
                last_usage = usage
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            # Only assemble visible text from delta.content. Thinking may live
            # in `reasoning` (or older `reasoning_content`); count it for
            # progress even when content stays 0 chars. Do not add extra
            # request fields to ask for reasoning.
            piece = delta.get("content")
            if piece:
                parts.append(piece)
                progress.add(len(piece))
                continue
            thinking = delta.get("reasoning") or delta.get("reasoning_content")
            if thinking:
                progress.add(len(thinking), thinking=True)
    if last_usage:
        usage_meter.add(last_usage)
    text = "".join(parts)
    if not text.strip():
        raise EmptyLLMResponse("Streaming response contained no content")
    return text


def _blocking_chat_completion(url, headers, payload) -> str:
    try:
        response = requests.post(
            url, headers=headers, json=_apply_max_tokens(payload), timeout=request_timeout
        )
        response_json = response.json()
        logger.info("RESPONSE:\n%s", json.dumps(response_json, indent=2))
        response.raise_for_status()
        usage_meter.add(parse_usage(response_json))
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


def _call_llm_provider(prompt: str, progress: _Progress, temperature: float = 0.7) -> str:
    """
    Call an OpenAI-compatible chat completions API.

    Defaults (when LLM_PROVIDER is unset): OPENROUTER + z-ai/glm-5.3-flash
    at https://openrouter.ai/api/v1/chat/completions.

    Environment variables:
    - LLM_PROVIDER: OPENROUTER (default), GEMINI (handled elsewhere), OLLAMA, XAI, ...
    - <provider>_MODEL / _BASE_URL / _API_KEY
    OPENROUTER_MODEL and OPENROUTER_BASE_URL have product defaults.
    OPENROUTER_API_KEY is required for the default provider.
    The path /v1/chat/completions is appended to the base URL (no trailing /v1 on the base).
    """
    provider = get_llm_provider()
    if provider == "GEMINI":
        raise ValueError("Gemini is handled by _call_llm_gemini; do not call _call_llm_provider")

    model_var = f"{provider}_MODEL"
    base_url_var = f"{provider}_BASE_URL"
    api_key_var = f"{provider}_API_KEY"

    if provider == "OPENROUTER":
        model = _clean_env(model_var, DEFAULT_OPENROUTER_MODEL)
        base_url = _clean_env(base_url_var, DEFAULT_OPENROUTER_BASE_URL)
        api_key = _clean_env(api_key_var)
        if not api_key:
            raise ValueError(format_error(missing_openrouter_key_message()))
    else:
        model = _clean_env(model_var)
        base_url = _clean_env(base_url_var)
        api_key = _clean_env(api_key_var)
        if not model:
            raise ValueError(format_error(f"{model_var} environment variable is required"))
        if not base_url:
            raise ValueError(format_error(f"{base_url_var} environment variable is required"))

    url = chat_completions_url(base_url)

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }

    try:
        if stream_enabled:
            try:
                return _stream_chat_completion(url, headers, payload, progress)
            except EmptyLLMResponse:
                if not stream_fallback_enabled:
                    raise
                # Opt-in second billed request — never silent.
                msg = format_error(
                    "empty streaming response; falling back to non-stream (extra LLM request)"
                )
                logger.warning(msg)
                _emit(msg)
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


# Default: OpenRouter z-ai/glm-5.3-flash. Gemini is opt-in via LLM_PROVIDER=GEMINI.
def call_llm(
    prompt: str,
    use_cache: bool = True,
    progress_label: str = None,
    temperature: float = 0.7,
) -> str:
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
    else:
        response_text = _call_llm_provider(prompt, progress, temperature=temperature)
    progress.done()

    # Log the response
    logger.info(f"RESPONSE: {response_text}")

    if not (response_text or "").strip():
        raise EmptyLLMResponse("LLM returned an empty response")

    # Update cache if enabled
    if use_cache:
        save_cache(prompt, response_text)

    _emit(usage_meter.format_line())
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
    provider = get_llm_provider()
    print(f"LLM_PROVIDER={provider}")
    if provider == "OPENROUTER":
        print(f"OPENROUTER_MODEL={_clean_env('OPENROUTER_MODEL', DEFAULT_OPENROUTER_MODEL)}")
        print(f"POST {chat_completions_url(_clean_env('OPENROUTER_BASE_URL', DEFAULT_OPENROUTER_BASE_URL))}")
    print("Making call...")
    try:
        response1 = call_llm(test_prompt, use_cache=False)
    except Exception as exc:
        print(format_error(exc))
        raise SystemExit(1) from exc
    print(f"Response: {response1}")
