"""Provider adapters that preserve one canonical Persona message envelope."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Optional

import httpx

from app.security.prompt import compression_system_prompt
from app.services.llm_interface import LLMInterface, build_stream_messages


class ProviderRequestError(RuntimeError):
    """Upstream failure without response bodies, prompts, or credentials."""

    def __init__(self, provider_id: str, status_code: int) -> None:
        super().__init__(f"{provider_id} request failed with status {status_code}")
        self.provider_id = provider_id
        self.status_code = status_code


class OpenAICompatibleAdapter(LLMInterface):
    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        default_model: str,
        message_builder: Callable[..., list[dict]],
        credential_required: bool = True,
        thinking_request_mode: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.model = default_model
        self._message_builder = message_builder
        self.credential_required = credential_required
        self.thinking_request_mode = thinking_request_mode
        self._transport = transport

    def _headers(self, api_key: str | None) -> dict[str, str]:
        if self.credential_required and not api_key:
            raise ValueError("provider credential is not configured")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _client(self, *, read_timeout: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(
                connect=5.0,
                read=read_timeout,
                write=10.0,
                pool=5.0,
            ),
        )

    async def get_chat_stream(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        isolated: bool = False,
        max_output_tokens: Optional[int] = None,
    ):
        messages = build_stream_messages(
            self._message_builder,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if tools is not None:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if self.thinking_request_mode == "reasoning_effort" and reasoning_effort:
            payload["thinking"] = {
                "type": (
                    "disabled"
                    if reasoning_effort in {"none", "minimal"}
                    else "enabled"
                )
            }
            payload["reasoning_effort"] = reasoning_effort

        async with self._client() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(api_key),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderRequestError(self.provider_id, response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    tool_calls = delta.get("tool_calls")
                    if content or tool_calls:
                        yield {"content": content, "tool_calls": tool_calls}

    async def _complete_text(
        self,
        messages: list[dict],
        *,
        api_key: str | None,
        model: str | None,
        temperature: float,
        response_format: dict[str, str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        async with self._client(read_timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(api_key),
                json=payload,
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    async def complete_json(
        self,
        messages: list[dict],
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> dict:
        raw = await self._complete_text(
            messages,
            api_key=api_key,
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("provider structured output was not a JSON object")
        return parsed

    async def translate_to_zh(
        self,
        text: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        if not text.strip():
            return ""
        # Q87 Slice B PROMPT-ENVELOPE: shared system+user (see _translation_messages).
        return await self._complete_text(
            _translation_messages(text),
            api_key=api_key,
            model=model,
            temperature=0.3,
        )

    async def compress_history(
        self,
        history: list,
        api_key: Optional[str] = None,
        old_summary: str = "",
        model: Optional[str] = None,
        identity_mode: str = "okabe",
    ) -> str:
        conversation = "\n".join(
            f"{'ユーザー' if message['role'] == 'user' else '紅莉栖'}: {message['content']}"
            for message in history
        )
        prompt = ""
        if old_summary:
            prompt += f"【これまでの作業コンテキスト】\n{old_summary}\n\n"
        prompt += f"【追加の会話履歴】\n{conversation}"
        system = compression_system_prompt(identity_mode)
        return await self._complete_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            api_key=api_key,
            model=model,
            temperature=0.3,
        )

    async def list_models(
        self,
        api_key: Optional[str] = None,
    ) -> list[str | dict[str, Any]]:
        async with self._client(read_timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(api_key),
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [
            row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]


def _translation_messages(text: str) -> list[dict]:
    from app.domain.subtitle_glossary_prompt import build_translation_messages

    return build_translation_messages(text)


def _compression_messages(
    history: list, old_summary: str, identity_mode: str = "okabe"
) -> list[dict]:
    conversation = "\n".join(
        f"{'ユーザー' if message['role'] == 'user' else '紅莉栖'}: {message['content']}"
        for message in history
    )
    prompt = ""
    if old_summary:
        prompt += f"【これまでの作業コンテキスト】\n{old_summary}\n\n"
    prompt += f"【追加の会話履歴】\n{conversation}"
    return [
        {
            "role": "system",
            "content": compression_system_prompt(identity_mode),
        },
        {"role": "user", "content": prompt},
    ]


def _json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("provider structured output was not a JSON object")
    return parsed


class OpenAIResponsesAdapter(LLMInterface):
    provider_id = "openai"
    base_url = "https://api.openai.com/v1"
    model = "gpt-5.6-luna"

    def __init__(
        self,
        *,
        message_builder: Callable[..., list[dict]],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._message_builder = message_builder
        self._transport = transport

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        if not api_key:
            raise ValueError("OpenAI credential is not configured")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _client(self, read_timeout: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0),
        )

    @staticmethod
    def _split_instructions(messages: list[dict]) -> tuple[str, list[dict]]:
        instructions = "\n\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        )
        inputs = [message for message in messages if message.get("role") != "system"]
        return instructions, inputs

    @staticmethod
    def _tools(tools: list | None) -> list[dict] | None:
        if tools is None:
            return None
        converted = []
        for tool in tools:
            function = tool.get("function", {})
            converted.append(
                {
                    "type": "function",
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters", {"type": "object"}),
                }
            )
        return converted

    async def get_chat_stream(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        isolated: bool = False,
        max_output_tokens: Optional[int] = None,
    ):
        messages = build_stream_messages(
            self._message_builder,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        instructions, inputs = self._split_instructions(messages)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "instructions": instructions,
            "input": inputs,
            "stream": True,
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        converted_tools = self._tools(tools)
        if converted_tools is not None:
            payload["tools"] = converted_tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

        async with self._client() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/responses",
                headers=self._headers(api_key),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderRequestError(self.provider_id, response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta" and event.get("delta"):
                        yield {"content": event["delta"], "tool_calls": None}
                    elif event_type == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            yield {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "index": event.get("output_index", 0),
                                        "id": item.get("call_id") or item.get("id"),
                                        "call_id": item.get("call_id"),
                                        "item_id": item.get("id"),
                                        "type": "function",
                                        "function": {
                                            "name": item.get("name"),
                                            "arguments": item.get("arguments", ""),
                                        },
                                    }
                                ],
                            }
                    elif event_type == "response.function_call_arguments.delta":
                        yield {
                            "content": None,
                            "tool_calls": [
                                {
                                    "index": event.get("output_index", 0),
                                    "id": event.get("call_id") or event.get("item_id"),
                                    "call_id": event.get("call_id"),
                                    "item_id": event.get("item_id"),
                                    "type": "function",
                                    "function": {
                                        "name": None,
                                        "arguments": event.get("delta", ""),
                                    },
                                }
                            ],
                        }

    @staticmethod
    def _output_text(payload: dict) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct.strip()
        parts = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()

    async def _complete_text(
        self,
        messages: list[dict],
        *,
        api_key: str | None,
        model: str | None,
        json_mode: bool = False,
    ) -> str:
        instructions, inputs = self._split_instructions(messages)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "instructions": instructions,
            "input": inputs,
        }
        if json_mode:
            payload["text"] = {"format": {"type": "json_object"}}
        async with self._client(30.0) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers=self._headers(api_key),
                json=payload,
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        return self._output_text(response.json())

    async def complete_json(
        self,
        messages: list[dict],
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> dict:
        return _json_object(
            await self._complete_text(
                messages,
                api_key=api_key,
                model=model,
                json_mode=True,
            )
        )

    async def translate_to_zh(self, text: str, api_key=None, model=None) -> str:
        if not text.strip():
            return ""
        return await self._complete_text(
            _translation_messages(text), api_key=api_key, model=model
        )

    async def compress_history(
        self,
        history: list,
        api_key=None,
        old_summary="",
        model=None,
        identity_mode: str = "okabe",
    ) -> str:
        return await self._complete_text(
            _compression_messages(history, old_summary, identity_mode),
            api_key=api_key,
            model=model,
        )

    async def list_models(
        self,
        api_key: Optional[str] = None,
    ) -> list[str | dict[str, Any]]:
        async with self._client(20.0) as client:
            response = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(api_key),
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        return [
            row
            for row in response.json().get("data", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]


class GeminiAdapter(LLMInterface):
    provider_id = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model = "gemini-3.5-flash"

    def __init__(
        self,
        *,
        message_builder: Callable[..., list[dict]],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._message_builder = message_builder
        self._transport = transport

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        if not api_key:
            raise ValueError("Gemini credential is not configured")
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _client(self, read_timeout: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0),
        )

    @staticmethod
    def _contents(messages: list[dict]) -> tuple[dict, list[dict]]:
        system_parts = []
        contents = []
        for message in messages:
            text = str(message.get("content", ""))
            role = message.get("role")
            if role == "system":
                system_parts.append(text)
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": text}],
                }
            )
        return {"parts": [{"text": "\n\n".join(system_parts)}]}, contents

    @staticmethod
    def _tools(tools: list | None) -> list[dict] | None:
        if tools is None:
            return None
        declarations = []
        for tool in tools:
            function = tool.get("function", {})
            declarations.append(
                {
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters", {"type": "object"}),
                }
            )
        return [{"functionDeclarations": declarations}]

    async def get_chat_stream(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        isolated: bool = False,
        max_output_tokens: Optional[int] = None,
    ):
        messages = build_stream_messages(
            self._message_builder,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        system_instruction, contents = self._contents(messages)
        payload: dict[str, Any] = {
            "systemInstruction": system_instruction,
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if max_output_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_output_tokens
        if reasoning_effort:
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": reasoning_effort
            }
        converted_tools = self._tools(tools)
        if converted_tools is not None:
            payload["tools"] = converted_tools
        async with self._client() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/models/{model or self.model}:streamGenerateContent",
                params={"alt": "sse"},
                headers=self._headers(api_key),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderRequestError(self.provider_id, response.status_code)
                event_seq = 0
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    candidates = event.get("candidates", [])
                    if not candidates:
                        continue
                    from app.services.turn_events import coalesce_gemini_parts

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        yield coalesce_gemini_parts(parts, stream_seq=event_seq)
                        event_seq += 1

    @staticmethod
    def _response_text(payload: dict) -> str:
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        ).strip()

    async def _complete_text(
        self,
        messages: list[dict],
        *,
        api_key: str | None,
        model: str | None,
        json_mode: bool = False,
    ) -> str:
        system_instruction, contents = self._contents(messages)
        config: dict[str, Any] = {"temperature": 0.2}
        if json_mode:
            config["responseMimeType"] = "application/json"
        async with self._client(30.0) as client:
            response = await client.post(
                f"{self.base_url}/models/{model or self.model}:generateContent",
                headers=self._headers(api_key),
                json={
                    "systemInstruction": system_instruction,
                    "contents": contents,
                    "generationConfig": config,
                },
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        return self._response_text(response.json())

    async def complete_json(self, messages: list[dict], api_key=None, model=None, temperature=0.1) -> dict:
        return _json_object(
            await self._complete_text(
                messages, api_key=api_key, model=model, json_mode=True
            )
        )

    async def translate_to_zh(self, text: str, api_key=None, model=None) -> str:
        if not text.strip():
            return ""
        return await self._complete_text(
            _translation_messages(text), api_key=api_key, model=model
        )

    async def compress_history(
        self,
        history: list,
        api_key=None,
        old_summary="",
        model=None,
        identity_mode: str = "okabe",
    ) -> str:
        return await self._complete_text(
            _compression_messages(history, old_summary, identity_mode),
            api_key=api_key,
            model=model,
        )

    async def list_models(
        self,
        api_key: Optional[str] = None,
    ) -> list[str | dict[str, Any]]:
        async with self._client(20.0) as client:
            response = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(api_key),
            )
        if response.status_code != 200:
            raise ProviderRequestError(self.provider_id, response.status_code)
        models = []
        for row in response.json().get("models", []):
            name = row.get("name") if isinstance(row, dict) else None
            methods = row.get("supportedGenerationMethods", []) if isinstance(row, dict) else []
            if isinstance(name, str) and (not methods or "generateContent" in methods):
                models.append({
                    "id": name.removeprefix("models/"),
                    "context_window": row.get("inputTokenLimit"),
                    "max_output_tokens": row.get("outputTokenLimit"),
                    "supported_generation_methods": methods,
                })
        return models
