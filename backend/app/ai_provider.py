"""AI provider adapters (Anthropic Claude and OpenRouter).

Both AnthropicModel and OpenRouterModel expose the same .generate_content(prompt) -> object.text
interface, so every call site across crawler.py / mcp_explorer.py / generator.py works
unchanged. Call sites get a model through create_ai_model(), which picks the provider from
settings.AI_PROVIDER.
"""
import json
import time
import requests

from .config import settings
from .database import update_scan_perf_data

OPENROUTER_ENDPOINT = "/chat/completions"


class AIProviderError(Exception):
    """Raised for any AI provider failure. Never carries the API key or Authorization header."""

    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


class _Response:
    """Mirrors the .text attribute shape of google.generativeai's response object."""

    def __init__(self, text: str):
        self.text = text


def _classify_http_error(status_code: int, body_text: str) -> str:
    lower_body = (body_text or "").lower()
    if status_code in (401, 403):
        return "invalid_api_key"
    if status_code == 429:
        return "rate_limit"
    if status_code == 400 and ("context length" in lower_body or "context_length" in lower_body or "maximum context" in lower_body):
        return "context_length_exceeded"
    if status_code == 404 or "model" in lower_body and "not found" in lower_body:
        return "model_unavailable"
    return "provider_error"


def _record_telemetry(scan_id, requested_model, actual_model, success, error_type, duration, usage,
                      provider: str = "OpenRouter"):
    if not scan_id:
        return
    try:
        from .database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
        row = cursor.fetchone()
        perf_data = {}
        if row and row["perf_data"]:
            try:
                perf_data = json.loads(row["perf_data"])
            except Exception:
                pass
        conn.close()

        ai_provider = perf_data.get("ai_provider", {
            "provider": provider,
            "requested_model": requested_model,
            "actual_model": None,
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "errors": [],
        })
        ai_provider["requested_model"] = requested_model
        ai_provider["calls"] += 1
        if success:
            ai_provider["successes"] += 1
            if actual_model:
                ai_provider["actual_model"] = actual_model
            if usage:
                ai_provider["input_tokens"] += usage.get("prompt_tokens", 0) or 0
                ai_provider["output_tokens"] += usage.get("completion_tokens", 0) or 0
        else:
            ai_provider["failures"] += 1
            errors = ai_provider.get("errors", [])
            errors.append({"type": error_type, "duration": round(duration, 2)})
            ai_provider["errors"] = errors[-10:]

        update_scan_perf_data(scan_id, {"ai_provider": ai_provider})
    except Exception:
        # Telemetry must never break the actual AI call.
        pass


class OpenRouterModel:
    def __init__(self, api_key: str, model_name: str = None, fallback_models=None, scan_id: str = None):
        self.api_key = api_key
        self.model_name = model_name or settings.OPENROUTER_PRIMARY_MODEL
        self.fallback_models = fallback_models or []
        self.scan_id = scan_id

    def _post(self, model_name: str, prompt: str):
        if not self.api_key:
            raise AIProviderError("No OpenRouter API key configured.", "missing_api_key")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8000,
        }

        try:
            resp = requests.post(
                f"{settings.OPENROUTER_BASE_URL}{OPENROUTER_ENDPOINT}",
                headers=headers,
                json=payload,
                timeout=settings.MCP_LLM_TIMEOUT,
            )
        except requests.Timeout:
            raise AIProviderError(f"OpenRouter request timed out for model '{model_name}'.", "timeout")
        except requests.RequestException as e:
            raise AIProviderError(f"OpenRouter request failed for model '{model_name}': {type(e).__name__}", "provider_error")

        if resp.status_code != 200:
            error_type = _classify_http_error(resp.status_code, resp.text)
            # Include OpenRouter's own response body (truncated) - it never echoes back the
            # Authorization header/key, and without it a generic "provider_error" bucket gives
            # no way to tell a bad model slug from a malformed payload from a real outage.
            body_excerpt = (resp.text or "").strip()[:300]
            raise AIProviderError(
                f"OpenRouter API error (status {resp.status_code}) for model '{model_name}': {error_type} - {body_excerpt}",
                error_type,
            )

        try:
            data = resp.json()
        except Exception:
            raise AIProviderError("OpenRouter returned a non-JSON response envelope.", "malformed_response")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIProviderError("OpenRouter response missing expected choices/message/content.", "malformed_response")

        actual_model = data.get("model", model_name)
        usage = data.get("usage") or {}
        return content, actual_model, usage

    def generate_content(self, prompt: str) -> _Response:
        models_to_try = [self.model_name] + list(self.fallback_models)
        last_error = None

        for idx, model_name in enumerate(models_to_try):
            start = time.perf_counter()
            try:
                content, actual_model, usage = self._post(model_name, prompt)
                duration = time.perf_counter() - start
                _record_telemetry(self.scan_id, self.model_name, actual_model, True, None, duration, usage)
                return _Response(content)
            except AIProviderError as e:
                duration = time.perf_counter() - start
                _record_telemetry(self.scan_id, self.model_name, None, False, e.error_type, duration, None)
                last_error = e
                # Only continue to a fallback model if one is actually configured next.
                if idx < len(models_to_try) - 1:
                    continue
                raise last_error

        raise last_error


# --- Anthropic (Claude) -------------------------------------------------------------------

# Server-side refusal fallback: if Claude Sonnet 5.5's safety classifiers decline a request,
# the API re-runs it on Anthropic's recommended fallback model inside the same call.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _classify_anthropic_error(exc) -> str:
    import anthropic
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "invalid_api_key"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.NotFoundError):
        return "model_unavailable"
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(getattr(exc, "message", "") or exc).lower()
        if "context" in msg and ("length" in msg or "window" in msg) or "too long" in msg:
            return "context_length_exceeded"
        return "bad_request"
    return "provider_error"


class AnthropicModel:
    """Calls Claude through the official Anthropic SDK (Messages API, streamed)."""

    def __init__(self, api_key: str, model_name: str = None, scan_id: str = None):
        import anthropic
        self.model_name = model_name or settings.ANTHROPIC_MODEL
        self.scan_id = scan_id
        # The SDK retries connection errors, 408/409/429 and 5xx with backoff on its own.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=settings.ANTHROPIC_TIMEOUT,
                                           max_retries=settings.ANTHROPIC_MAX_RETRIES)

    def generate_content(self, prompt: str) -> _Response:
        import anthropic
        start = time.perf_counter()
        try:
            # Streamed (with get_final_message) so long generations never hit an HTTP timeout.
            with self._client.beta.messages.stream(
                model=self.model_name,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                output_config={"effort": settings.ANTHROPIC_EFFORT},
                betas=[ANTHROPIC_FALLBACK_BETA],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as e:
            duration = time.perf_counter() - start
            error_type = _classify_anthropic_error(e)
            _record_telemetry(self.scan_id, self.model_name, None, False, error_type, duration, None, provider="Anthropic")
            raise AIProviderError(f"Anthropic API error for model '{self.model_name}': {error_type} - {str(e)[:300]}",
                                  error_type) from e

        duration = time.perf_counter() - start
        if message.stop_reason == "refusal":
            _record_telemetry(self.scan_id, self.model_name, message.model, False, "refusal", duration, None, provider="Anthropic")
            category = getattr(getattr(message, "stop_details", None), "category", None)
            raise AIProviderError(f"Claude declined the request (category={category}).", "refusal")

        text = "".join(block.text for block in message.content if block.type == "text")
        if message.stop_reason == "max_tokens":
            # A truncated answer is almost always invalid JSON here - fail loudly instead of
            # handing a half-written payload to the parsers.
            _record_telemetry(self.scan_id, self.model_name, message.model, False, "max_tokens", duration, None, provider="Anthropic")
            raise AIProviderError(f"Claude response hit max_tokens ({settings.ANTHROPIC_MAX_TOKENS}) and was truncated.",
                                  "max_tokens")

        usage = {"prompt_tokens": message.usage.input_tokens, "completion_tokens": message.usage.output_tokens}
        _record_telemetry(self.scan_id, self.model_name, message.model, True, None, duration, usage, provider="Anthropic")
        return _Response(text)


# --- Provider selection ---------------------------------------------------------------------
#
# Bring-your-own-key: each user can enter their own key in the frontend, and its prefix decides
# the provider. A server-side key (ANTHROPIC_API_KEY / OPENROUTER_API_KEY, per AI_PROVIDER) is
# only an optional fallback for requests that don't bring one. User keys live in memory for the
# duration of one scan only - they are never written to the database, logs, or perf data.

ANTHROPIC_KEY_PREFIX = "sk-ant-"
OPENROUTER_KEY_PREFIX = "sk-or-"


def detect_provider(api_key: str):
    """Returns "Anthropic" / "OpenRouter" from the key's prefix, or None if unrecognized."""
    key = (api_key or "").strip()
    if key.startswith(ANTHROPIC_KEY_PREFIX):
        return "Anthropic"
    if key.startswith(OPENROUTER_KEY_PREFIX):
        return "OpenRouter"
    return None


def _default_provider() -> str:
    return "Anthropic" if settings.AI_PROVIDER.strip().lower() == "anthropic" else "OpenRouter"


def _model_for(provider: str) -> str:
    return settings.ANTHROPIC_MODEL if provider == "Anthropic" else settings.OPENROUTER_PRIMARY_MODEL


def resolve_ai(request_api_key: str = None):
    """Returns (provider, api_key_or_None, model_name, key_source) for one scan.

    key_source is "user" (key typed in the frontend), "server" (configured fallback) or "none".
    A user key with an unrecognized prefix falls back to the default provider here; the API
    layer rejects such keys up front with a 400, so this only matters for direct callers.
    """
    user_key = (request_api_key or "").strip()
    if user_key:
        provider = detect_provider(user_key) or _default_provider()
        return provider, user_key, _model_for(provider), "user"
    provider = _default_provider()
    server_key = settings.ANTHROPIC_API_KEY if provider == "Anthropic" else settings.OPENROUTER_API_KEY
    if server_key:
        return provider, server_key, _model_for(provider), "server"
    return provider, None, _model_for(provider), "none"


def server_key_configured() -> bool:
    return resolve_ai(None)[3] == "server"


def ai_provider_name(request_api_key: str = None) -> str:
    return resolve_ai(request_api_key)[0]


def ai_model_name(request_api_key: str = None) -> str:
    return resolve_ai(request_api_key)[2]


def create_ai_model(scan_id: str = None, request_api_key: str = None):
    """Returns a model for this scan's provider/key, or None when no key is available at all."""
    provider, key, _, _ = resolve_ai(request_api_key)
    if not key:
        return None
    if provider == "Anthropic":
        return AnthropicModel(api_key=key, scan_id=scan_id)
    return OpenRouterModel(api_key=key, model_name=settings.OPENROUTER_PRIMARY_MODEL,
                           fallback_models=[m.strip() for m in settings.OPENROUTER_FALLBACK_MODELS.split(",") if m.strip()],
                           scan_id=scan_id)
