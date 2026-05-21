"""
LLM provider and model configuration.

Supports:
- OpenAI: gpt-4o, gpt-4o-mini, etc.
- Google: gemini-3-pro, gemini-3-pro-preview, gemini-1.5-pro, etc.
- OpenRouter: any model via openrouter.ai (e.g. anthropic/claude-sonnet-4, meta-llama/llama-3-70b)

Single entry point: chat_completion(messages, model, temperature, provider) -> {"content", "usage", "model"}.
"""

import os
import time
from typing import Literal, Tuple, List, Dict, Any, Optional

Provider = Literal["openai", "google", "openrouter", "gateway"]

# Defaults (can be overridden by env: LLM_PROVIDER, LLM_MODEL)
DEFAULT_PROVIDER: Provider = "openai"
DEFAULT_MODEL = "gpt-5.2"

# Alias -> API model name (for Google)
GOOGLE_MODEL_ALIASES = {
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3-flash": "gemini-3-flash-preview",
}
OPENAI_MODEL_ALIASES = {
    "gpt-4o": "gpt-4o-2024-08-06",
    "gpt-4o-mini": "gpt-4o-mini-2024-07-18",
    "gpt-5-mini": "gpt-5-mini-2025-08-07",
    "gpt-5.2": "gpt-5.2-2025-12-11",
}

# Embedding: Step 2 similarity uses embeddings; keep OpenAI by default
EMBEDDING_MODEL_OPENAI = "text-embedding-3-small"

# Gemini: no wait on 429 (set RETRIES > 1 and WAIT_SECONDS > 0 to re-enable retry with backoff)
GEMINI_DELAY_BETWEEN_CALLS = 2.0
GEMINI_429_RETRIES = 1
GEMINI_429_WAIT_SECONDS = 0


def _resolve_gateway_base() -> str:
    """Return CMU LiteLLM gateway base URL from common env vars, or empty string."""
    for key in ("LITELLM_API_BASE", "LITELLM_PROXY_API_BASE", "OPENAI_API_BASE"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def _resolve_gateway_key() -> str:
    """Return API key for the gateway proxy."""
    for key in ("LITELLM_API_KEY", "LITELLM_PROXY_API_KEY"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return os.environ.get("OPENAI_API_KEY", "").strip()


def load_secrets_toml(path: str | None = None):
    """Load secrets.toml (CoGym style) into os.environ if not already set."""
    import toml as _toml
    from pathlib import Path
    if path is None:
        _pkg_root = Path(__file__).resolve().parent.parent
        candidates = [
            _pkg_root / "secrets.toml",
            Path("/mnt/nas3/eunsu/collaborative-gym/secrets.toml"),
        ]
        for c in candidates:
            if c.is_file():
                path = str(c)
                break
    if path is None:
        return
    try:
        data = _toml.load(path)
    except Exception:
        return
    for k, v in data.items():
        if k not in os.environ or not os.environ[k].strip():
            os.environ[k] = str(v)


def get_provider_and_model(
    provider: str | None = None,
    model: str | None = None,
) -> Tuple[Provider, str]:
    """
    Resolve provider and model from args or env.
    - If model contains 'wine-', provider is forced to 'gateway' (CMU LiteLLM proxy).
    - If model looks like 'gemini-*', provider is forced to 'google'.
    - Otherwise provider from arg or env or default; model from arg or env or default.
    """
    provider = (provider or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    model = (model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL).strip()
  
    model_lower = model.lower()

    if "wine-" in model_lower:
        provider = "gateway"
    elif model_lower.startswith("gemini-"):
        provider = "google"
        model = GOOGLE_MODEL_ALIASES.get(model_lower, model)
    elif provider == "google" and not model_lower.startswith("gemini-"):
        model = GOOGLE_MODEL_ALIASES.get("gemini-3-pro", "gemini-3-pro-preview")
    elif provider == "openrouter":
        pass
    elif provider == "openai":
        model = OPENAI_MODEL_ALIASES.get(model_lower, model)

    return provider, model


# ---------------------------------------------------------------------------
# Optional backend imports
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_AVAILABLE = False


# Explicit default so env OPENAI_BASE_URL is ignored (avoids duplicate /v1/chat/completions path)
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def get_openai_client():
    """Return an OpenAI client (default base_url only; ignores OPENAI_BASE_URL env)."""
    if not _OPENAI_AVAILABLE or OpenAI is None:
        raise ImportError("openai package required. Install with: pip install openai")
    return OpenAI(base_url=OPENAI_DEFAULT_BASE)


def _is_rate_limit(err: BaseException) -> bool:
    s = str(err).upper()
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "RATE_LIMIT" in s or "QUOTA" in s


def _messages_to_gemini(messages: List[Dict[str, str]]) -> Tuple[Optional[str], str]:
    """Convert OpenAI-format messages to (system_instruction, user_text) for Gemini."""
    system_parts = []
    user_parts = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = (m.get("content") or "").strip()
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            user_parts.append(content)
    system_instruction = "\n".join(system_parts).strip() or None
    user_text = "\n".join(user_parts).strip() or " "
    return system_instruction, user_text


def _call_openai(messages: List[Dict], model: str, temperature: float, api_key: Optional[str] = None) -> Dict[str, Any]:
    if not _OPENAI_AVAILABLE or OpenAI is None:
        raise ImportError("openai package required. Install with: pip install openai")
    kwargs = {"base_url": OPENAI_DEFAULT_BASE}
    if api_key is not None:
        kwargs["api_key"] = api_key
    client = OpenAI(**kwargs)

    r = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    content = r.choices[0].message.content or ""
    usage = None
    if r.usage:
        usage = {
            "prompt_tokens": r.usage.prompt_tokens,
            "completion_tokens": r.usage.completion_tokens,
            "total_tokens": r.usage.total_tokens,
        }
    return {"content": content, "usage": usage, "model": model}


def _call_gemini(messages: List[Dict], model: str, temperature: float) -> Dict[str, Any]:
    import google.generativeai as genai
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    model_name = model
    gen_model = genai.GenerativeModel(model_name)
    system_instruction, user_text = _messages_to_gemini(messages)
    attempt = 3 # max 3 attempts

    last_err = None
    for attempt in range(attempt):
        response = gen_model.generate_content(
            user_text,
            generation_config={"temperature": temperature},)


        text = getattr(response, "text", None)
        usage = None
        um = getattr(response, "usage_metadata", None)
        if um:
            usage = {
                "prompt_tokens": getattr(um, "prompt_token_count", 0),
                "completion_tokens": getattr(um, "candidates_token_count", 0),
                "total_tokens": getattr(um, "total_token_count", 0),
            }
        return {"content": text, "usage": usage, "model": model_name}

    raise RuntimeError(f"Gemini API error (rate limit): {last_err}") from last_err


GATEWAY_429_RETRIES = 5
GATEWAY_429_WAIT_SECONDS = 10


def _call_gateway(messages: List[Dict], model: str, temperature: float) -> Dict[str, Any]:
    """Call CMU LiteLLM gateway (OpenAI-compatible proxy) with wine-* deployment ids."""
    if not _OPENAI_AVAILABLE or OpenAI is None:
        raise ImportError("openai package required. Install with: pip install openai")
    load_secrets_toml()
    api_base = _resolve_gateway_base()
    api_key = _resolve_gateway_key()
    if not api_base or not api_key:
        raise ValueError(
            f"Gateway model {model!r} requires LITELLM_API_BASE (or LITELLM_PROXY_API_BASE / OPENAI_API_BASE) "
            "and LITELLM_API_KEY (or LITELLM_PROXY_API_KEY). "
            "Set them in env or collaborative-gym/secrets.toml."
        )
    client = OpenAI(base_url=api_base, api_key=api_key)
    litellm_model = model if ("/" in model or model.startswith("wine-")) else f"openai/{model}"

    last_err = None
    for attempt in range(GATEWAY_429_RETRIES):
        try:
            r = client.chat.completions.create(
                model=litellm_model,
                messages=messages,
                temperature=temperature,
            )
            content = r.choices[0].message.content or ""
            usage = None
            if r.usage:
                usage = {
                    "prompt_tokens": r.usage.prompt_tokens,
                    "completion_tokens": r.usage.completion_tokens,
                    "total_tokens": r.usage.total_tokens,
                }
            return {"content": content, "usage": usage, "model": model}
        except Exception as err:
            if _is_rate_limit(err) and attempt < GATEWAY_429_RETRIES - 1:
                wait = GATEWAY_429_WAIT_SECONDS * (attempt + 1)
                print(f"  [Gateway] 429 rate limit, retrying in {wait}s (attempt {attempt+1}/{GATEWAY_429_RETRIES})...")
                time.sleep(wait)
                last_err = err
            else:
                raise
    raise RuntimeError(f"Gateway rate limit after {GATEWAY_429_RETRIES} retries: {last_err}") from last_err


OPENROUTER_429_RETRIES = 5
OPENROUTER_429_WAIT_SECONDS = 10


def _call_openrouter(messages: List[Dict], model: str, temperature: float) -> Dict[str, Any]:
    if not _OPENAI_AVAILABLE or OpenAI is None:
        raise ImportError("openai package required. Install with: pip install openai")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required for openrouter provider")
    client = OpenAI(base_url=OPENROUTER_DEFAULT_BASE, api_key=api_key)

    last_err = None
    for attempt in range(OPENROUTER_429_RETRIES):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            content = r.choices[0].message.content or ""
            usage = None
            if r.usage:
                usage = {
                    "prompt_tokens": r.usage.prompt_tokens,
                    "completion_tokens": r.usage.completion_tokens,
                    "total_tokens": r.usage.total_tokens,
                }
            return {"content": content, "usage": usage, "model": model}
        except Exception as err:
            if _is_rate_limit(err) and attempt < OPENROUTER_429_RETRIES - 1:
                wait = OPENROUTER_429_WAIT_SECONDS * (attempt + 1)
                print(f"  [OpenRouter] 429 rate limit, retrying in {wait}s (attempt {attempt+1}/{OPENROUTER_429_RETRIES})...")
                time.sleep(wait)
                last_err = err
            else:
                raise
    raise RuntimeError(f"OpenRouter rate limit after {OPENROUTER_429_RETRIES} retries: {last_err}") from last_err


def _sanitize_message_content(s: str) -> str:
    """Ensure content is valid for JSON/API: remove control chars, fix encoding."""
    if not isinstance(s, str):
        s = str(s)
    try:
        s = s.encode("utf-8", errors="replace").decode("utf-8")
    except Exception:
        s = s.encode("ascii", errors="replace").decode("ascii")
    # Strip control characters (including \x00) that can break JSON / API
    return "".join(c for c in s if (ord(c) >= 32 or c in "\n\r\t"))


def _sanitize_messages(messages: List[Dict]) -> List[Dict]:
    """Return a copy of messages with content sanitized for API request."""
    out = []
    for m in messages:
        m = dict(m)
        if "content" in m and m["content"] is not None:
            m["content"] = _sanitize_message_content(m["content"])
        out.append(m)
    return out


def chat_completion(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 1,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Single entry point for chat completion across providers.
    messages: OpenAI-format list of {"role": "system"|"user"|"assistant", "content": "..."}.
    model: e.g. gpt-4o, gemini-1.5-pro, gemini-3-pro.
    provider: "openai", "google", or "openrouter". If None, inferred from model (gemini-* -> google).
    api_key: optional override for OpenAI (e.g. OPENAI_API_KEY_2). Only used when provider is openai.
    Returns: {"content": str, "usage": dict|None, "model": str}.
    """
    prov, resolved_model = get_provider_and_model(provider=provider, model=model)
    messages = _sanitize_messages(messages)

    if prov == "gateway":
        return _call_gateway(messages, resolved_model, temperature)
    if prov == "openai":
        return _call_openai(messages, resolved_model, temperature, api_key=api_key)
    if prov == "google":
        return _call_gemini(messages, resolved_model, temperature)
    if prov == "openrouter":
        return _call_openrouter(messages, resolved_model, temperature)
    raise ValueError(f"Unknown provider: {prov}. Use 'openai', 'google', 'openrouter', or 'gateway'.")


