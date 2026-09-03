"""OpenRouter AI provider adapter.

Exposes OpenRouterModel with the same .generate_content(prompt) -> object.text
interface as google.generativeai's GenerativeModel, so every existing call site
across crawler.py / mcp_explorer.py / generator.py works unchanged.
"""
import json
import time
import requests

from .config import settings
from .database import update_scan_perf_data

OPENROUTER_ENDPOINT = "/chat/completions"


class AIProviderError(Exception):
    """Raised for any OpenRouter failure. Never carries the API key or Authorization header."""

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


def _record_telemetry(scan_id, requested_model, actual_model, success, error_type, duration, usage):
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
            "provider": "OpenRouter",
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
            raise AIProviderError(
                f"OpenRouter API error (status {resp.status_code}) for model '{model_name}': {error_type}",
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
