"""Provider adapter for the course's Anthropic-shaped agent loops.

The chapter code teaches one internal protocol:

    response.content -> text/tool_use blocks
    tool results      -> user-side tool_result blocks

Anthropic's Messages API already speaks that protocol. OpenAI-compatible
providers such as SenseNova use chat/completions and tool_calls instead.
This module translates only at the network boundary so the teaching loops
can stay unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from types import SimpleNamespace
from typing import Any

import httpx


@dataclass
class TextBlock:
    type: str
    text: str


@dataclass
class ToolUseBlock:
    type: str
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class MessageResponse:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str
    model: str | None = None
    usage: Any = None


class ProviderAPIError(RuntimeError):
    """HTTP or protocol error returned by an OpenAI-compatible provider."""


def _value(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            item_type = _value(item, "type")
            if item_type == "text":
                parts.append(str(_value(item, "text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(
            str(_value(block, "text", ""))
            for block in system
            if _value(block, "type") == "text"
        )
    return str(system) if system else ""


def _assistant_message(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        return {"role": "assistant", "content": content}

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        block_type = _value(block, "type")
        if block_type == "text":
            text = str(_value(block, "text", ""))
            if text:
                text_parts.append(text)
        elif block_type == "tool_use":
            tool_calls.append({
                "id": str(_value(block, "id", "")),
                "type": "function",
                "function": {
                    "name": str(_value(block, "name", "")),
                    "arguments": json.dumps(
                        _value(block, "input", {}) or {},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            })

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _user_messages(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"role": "user", "content": content}]
    if not isinstance(content, list):
        return [{"role": "user", "content": _stringify_content(content)}]

    tool_messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in content:
        block_type = _value(block, "type")
        if block_type == "tool_result":
            tool_messages.append({
                "role": "tool",
                "tool_call_id": str(_value(block, "tool_use_id", "")),
                "content": _stringify_content(_value(block, "content", "")),
            })
        elif block_type == "text":
            text = str(_value(block, "text", ""))
            if text:
                text_parts.append(text)
        elif isinstance(block, str):
            text_parts.append(block)

    if text_parts:
        tool_messages.append({"role": "user", "content": "\n".join(text_parts)})
    return tool_messages or [{"role": "user", "content": ""}]


def to_openai_messages(system: Any, messages: list[dict]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    system_content = _system_text(system)
    if system_content:
        converted.append({"role": "system", "content": system_content})

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "assistant":
            converted.append(_assistant_message(content))
        elif role == "user":
            converted.extend(_user_messages(content))
        elif role == "tool":
            converted.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "content": _stringify_content(content),
            })
        elif role == "system":
            converted.append({"role": "system", "content": _stringify_content(content)})
    return converted


def to_openai_tools(tools: list[dict] | None) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return converted


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderAPIError(f"Invalid tool arguments from provider: {raw!r}") from exc
    if not isinstance(parsed, dict):
        raise ProviderAPIError("Tool arguments must decode to a JSON object")
    return parsed


def from_openai_response(payload: dict[str, Any]) -> MessageResponse:
    # Some native gateways wrap the OpenAI-shaped response in "data".
    if isinstance(payload.get("data"), dict) and "choices" in payload["data"]:
        payload = payload["data"]

    choices = payload.get("choices") or []
    if not choices:
        raise ProviderAPIError("Provider response did not contain choices")

    choice = choices[0]
    message = choice.get("message") or {}
    blocks: list[TextBlock | ToolUseBlock] = []

    text = _stringify_content(message.get("content"))
    if text:
        blocks.append(TextBlock(type="text", text=text))

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        blocks.append(ToolUseBlock(
            type="tool_use",
            id=str(call.get("id", "")),
            name=str(function.get("name", "")),
            input=_parse_arguments(function.get("arguments", "{}")),
        ))

    finish_reason = choice.get("finish_reason")
    if any(block.type == "tool_use" for block in blocks):
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return MessageResponse(
        content=blocks,
        stop_reason=stop_reason,
        model=payload.get("model"),
        usage=SimpleNamespace(**payload.get("usage", {}))
        if isinstance(payload.get("usage"), dict) else payload.get("usage"),
    )


class _OpenAIMessages:
    def __init__(self, owner: "OpenAICompatibleClient"):
        self._owner = owner

    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: Any = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> MessageResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": max_tokens,
        }
        openai_tools = to_openai_tools(tools)
        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = "auto"

        for optional in ("temperature", "top_p", "stop"):
            if optional in kwargs and kwargs[optional] is not None:
                body[optional] = kwargs[optional]

        try:
            response = self._owner._http.post(
                f"{self._owner.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._owner.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise ProviderAPIError("Provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderAPIError(f"Provider connection error: {exc}") from exc

        if response.status_code >= 400:
            try:
                error_payload = response.json()
                detail = error_payload.get("error", error_payload)
            except Exception:
                detail = response.text[:500]
            raise ProviderAPIError(
                f"Provider HTTP {response.status_code}: {detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderAPIError("Provider returned non-JSON response") from exc
        return from_openai_response(payload)


class OpenAICompatibleClient:
    """Expose an Anthropic-like .messages.create over chat/completions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 120,
        http_client: Any = None,
    ):
        if not api_key:
            raise RuntimeError(
                "Missing API key. Set OPENAI_API_KEY or ANTHROPIC_API_KEY."
            )
        if not base_url:
            raise RuntimeError(
                "Missing base URL. Set OPENAI_BASE_URL or ANTHROPIC_BASE_URL."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=timeout)
        self.messages = _OpenAIMessages(self)


def create_client(anthropic_cls: Any):
    """Create the configured provider client.

    LLM_API_STYLE=anthropic (default)
        Use the repository's original Anthropic SDK path.

    LLM_API_STYLE=openai
        Translate the chapter's Anthropic-shaped loop to an OpenAI-compatible
        chat/completions endpoint. SenseNova's Token Plan endpoint uses this.
    """
    style = os.getenv("LLM_API_STYLE", "").strip().lower()
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")

    # Friendly auto-detection for existing SenseNova Token Plan .env files.
    if not style:
        style = "openai" if "token.sensenova.cn" in (base_url or "") else "anthropic"

    if style == "anthropic":
        return anthropic_cls(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    if style not in {"openai", "openai-compatible", "sensenova"}:
        raise RuntimeError(
            f"Unsupported LLM_API_STYLE={style!r}; use anthropic or openai"
        )

    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or ""
    )
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url or "",
        timeout=timeout,
    )
