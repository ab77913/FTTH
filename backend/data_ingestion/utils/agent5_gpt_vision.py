"""Optional GPT vision helper for Agent 5 house-number search.

The iterative Street View search treats this module as advisory. If no Azure
OpenAI/OpenAI vision credentials are configured, GPT vision is simply disabled
and the deterministic OCR/search path continues.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import requests

from data_ingestion.utils.agent5_logging import log_api_call
from data_ingestion.utils.json_utils import parse_llm_json_object

logger = logging.getLogger(__name__)

_DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def gpt_vision_enabled() -> bool:
    return vision_llm_enabled()


def vision_llm_enabled(provider: str | None = None) -> bool:
    provider = (provider or os.environ.get("AGENT5_LLM_PROVIDER") or "online").strip().lower()
    if provider in {"0", "false", "no", "off", "disabled", "none"}:
        return False
    if os.environ.get("AGENT5_GPT_VISION", "1").lower() in {"0", "false", "no", "off"}:
        return False
    if provider in {"offline", "ollama", "local"}:
        return bool(_env("OLLAMA_HOST") or "http://127.0.0.1:11434")
    return bool(
        (
            _env("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL")
            and _env("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY")
            and _env("AZURE_OPENAI_GPT_VISION_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_MODEL")
        )
        or (_env("OPENAI_API_KEY") and _env("OPENAI_VISION_MODEL", "OPENAI_MODEL"))
    )


def _digits_only(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def gpt_house_number_matches(
    gpt_result: dict[str, Any] | None,
    expected_house_number: str,
) -> tuple[bool, float]:
    if not gpt_result:
        return False, 0.0
    expected = _digits_only(expected_house_number)
    if not expected:
        return False, 0.0

    candidates = [
        gpt_result.get("house_number_text"),
        gpt_result.get("house_number"),
        gpt_result.get("recognized"),
    ]
    for item in gpt_result.get("candidates") or []:
        if isinstance(item, dict):
            candidates.append(item.get("text") or item.get("house_number"))
        else:
            candidates.append(item)

    confidence = _as_float(gpt_result.get("confidence"), 0.0)
    for candidate in candidates:
        if _digits_only(candidate) == expected:
            return True, max(0.0, min(1.0, confidence or 0.70))
    return False, max(0.0, min(1.0, confidence))


def analyze_house_number_view(
    image_bytes: bytes,
    expected_house_number: str,
    address: str = "",
    *,
    provider: str | None = None,
    ollama_model: str | None = None,
) -> dict[str, Any] | None:
    """Ask a configured vision model to read/guidance-score one Street View image."""
    provider = (provider or os.environ.get("AGENT5_LLM_PROVIDER") or "online").strip().lower()
    if not image_bytes or not vision_llm_enabled(provider):
        return None

    prompt = (
        "Analyze this Google Street View image for FTTH address validation. "
        f"Expected house number: {expected_house_number!r}. Address: {address!r}. "
        "Return only JSON with keys: house_number_visible (bool), house_number_text "
        "(string), confidence (0..1), obstruction (bool), obstruction_type (string: "
        "none/tree/bush/vehicle/sign/other), recommended_fov (integer 10..60), "
        "recommended_heading_delta_deg (number -15..15), recommended_move_meters "
        "({forward:number,lateral:number}), and notes (short string)."
    )

    payload = _chat_payload(prompt, image_bytes)
    try:
        if provider in {"offline", "ollama", "local"}:
            data, status = _post_ollama(prompt, image_bytes, model=ollama_model)
        elif _env("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL"):
            data, status = _post_azure(payload)
        else:
            data, status = _post_openai(payload)
        log_api_call(
            logger,
            f"{provider}_vision_llm",
            request=_safe_request_preview(payload),
            response=data,
            status=status,
        )
        content = _extract_message_content(data)
        parsed = parse_llm_json_object(content)
        return _normalize_result(parsed)
    except Exception as exc:
        log_api_call(
            logger,
            f"{provider}_vision_llm",
            request=_safe_request_preview(payload),
            response=str(exc),
            status="error",
        )
        logger.debug("Agent5 GPT vision unavailable/error: %s", exc)
        return None


def _post_ollama(prompt: str, image_bytes: bytes, *, model: str | None = None) -> tuple[dict[str, Any], int]:
    ollama_url = _env("OLLAMA_HOST") or "http://127.0.0.1:11434"
    model_name = (model or _env("OLLAMA_VISION_MODEL") or "qwen2.5vl:latest").strip()
    body = {
        "model": model_name,
        "prompt": prompt,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 400},
    }
    resp = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=body, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    response_text = data.get("response", "")
    return {
        "provider": "ollama",
        "model": model_name,
        "response": response_text,
        "choices": [{"message": {"content": response_text}}],
        "raw": data,
    }, resp.status_code


def _chat_payload(prompt: str, image_bytes: bytes) -> dict[str, Any]:
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }


def _post_azure(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    endpoint = _env("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL").rstrip("/")
    key = _env("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY")
    deployment = _env(
        "AZURE_OPENAI_GPT_VISION_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_MODEL",
    )
    api_version = _env("AZURE_OPENAI_API_VERSION") or _DEFAULT_AZURE_API_VERSION
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
    resp = requests.post(
        url,
        params={"api-version": api_version},
        headers={"api-key": key, "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json(), resp.status_code


def _post_openai(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    model = _env("OPENAI_VISION_MODEL", "OPENAI_MODEL")
    body = {"model": model, **payload}
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {_env('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        json=body,
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json(), resp.status_code


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = ((choices[0] or {}).get("message") or {}).get("content", "")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _normalize_result(data: dict[str, Any]) -> dict[str, Any]:
    move = data.get("recommended_move_meters") or {}
    if not isinstance(move, dict):
        move = {}
    result = {
        "house_number_visible": bool(data.get("house_number_visible")),
        "house_number_text": str(data.get("house_number_text") or data.get("house_number") or "").strip(),
        "confidence": max(0.0, min(1.0, _as_float(data.get("confidence"), 0.0))),
        "obstruction": bool(data.get("obstruction")),
        "obstruction_type": str(data.get("obstruction_type") or "none").strip().lower() or "none",
        "recommended_fov": _as_int(data.get("recommended_fov"), 30, 10, 60),
        "recommended_heading_delta_deg": max(
            -15.0, min(15.0, _as_float(data.get("recommended_heading_delta_deg"), 0.0))
        ),
        "recommended_move_meters": {
            "forward": max(-10.0, min(10.0, _as_float(move.get("forward"), 0.0))),
            "lateral": max(-10.0, min(10.0, _as_float(move.get("lateral"), 0.0))),
        },
        "notes": str(data.get("notes") or "")[:300],
    }
    return result


def _safe_request_preview(payload: dict[str, Any]) -> dict[str, Any]:
    preview = dict(payload)
    preview["messages"] = "<vision prompt + image>"
    return preview


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))
