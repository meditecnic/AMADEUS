import json
import httpx
from typing import Optional
from app import config
from app.services.llm_interface import LLMInterface, build_stream_messages
from app.services.model_catalog import model_catalog
from app.services.persona_bible import persona_bible
from app.services.provider_adapters import ProviderRequestError


class DeepSeekTranslationError(RuntimeError):
    """Translation request failure with a retry-relevant HTTP status only."""

    def __init__(self, status_code: int | None = None) -> None:
        super().__init__("translation_unavailable")
        self.status_code = status_code


def build_chat_payload(
    *,
    model: str,
    messages: list[dict],
    temperature: float | None,
    tools: Optional[list],
    tool_choice,
    reasoning_effort: Optional[str],
    max_output_tokens: Optional[int] = None,
) -> dict:
    """Build a DeepSeek request; never combine V4 thinking with tools.

    Amadeus tool architecture injects tool results as untrusted system evidence
    and never round-trips assistant reasoning_content. DeepSeek thinking mode
    with tools requires that field on the next turn, so any tools-bearing
    request must disable thinking. Final answer requests (no tools) restore the
    user-selected reasoning_effort.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature if temperature is not None else 0.7,
    }
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens

    uses_thinking = model_catalog.uses_native_reasoning("deepseek", model)
    has_tools = tools is not None
    forced_tool_choice = (
        has_tools
        and tool_choice is not None
        and tool_choice != "auto"
    )
    if uses_thinking:
        # Close thinking whenever tools are present (auto or forced). Only the
        # post-tool final answer request re-enables native thinking + effort.
        payload["thinking"] = {
            "type": "disabled" if has_tools else "enabled"
        }
        if reasoning_effort and not has_tools:
            payload["reasoning_effort"] = reasoning_effort

    if has_tools:
        payload["tools"] = tools
        if tool_choice is not None and (not uses_thinking or forced_tool_choice):
            payload["tool_choice"] = tool_choice

    return payload


class DeepSeekService(LLMInterface):
    def __init__(self):
        self.api_key = ""
        self.model = config.DEEPSEEK_MODEL  # defaults to catalog preferred request ID
        self.base_url = "https://api.deepseek.com"
        
        self.fewshot_path = config.PROMPTS_DIR / "kurisu_fewshot.json"
        
        self._system_prompt = self._load_system_prompt()
        self._fewshot = self._load_fewshot()

    def _load_system_prompt(self) -> str:
        from app.security.prompt import SYSTEM_PROMPT_BASE

        return SYSTEM_PROMPT_BASE

    def _load_fewshot(self) -> list:
        if self.fewshot_path.exists():
            with open(self.fewshot_path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise FileNotFoundError(f"Few-shot JSON not found at {self.fewshot_path}")

    def _load_anchor_clause(self, identity_mode: str = "okabe") -> str:
        # Default okabe for legacy; chat path usually already has full Bible in system_prompt.
        return persona_bible.render_provider_anchor(identity_mode)

    def get_anchored_system_prompt(
        self,
        base_prompt: str = None,
        identity_mode: str = "okabe",
    ) -> str:
        prompt = base_prompt if base_prompt is not None else self._system_prompt
        if "PERSONA BIBLE v1 (canonical runtime persona)" in prompt:
            # Full Bible already carries mode rules from compile; output contract only.
            anchor = persona_bible.render_output_anchor()
        else:
            # S3c: product default base (_system_prompt / SYSTEM_PROMPT_BASE) is okabe
            # text; re-resolve by mode so self does not get okabe frame + self anchor.
            # Custom bases are left unchanged (resolve_system_prompt_base contract).
            from app.security.prompt import resolve_system_prompt_base

            prompt = resolve_system_prompt_base(prompt, identity_mode)
            anchor = self._load_anchor_clause(identity_mode)
        return prompt.strip() + "\n" + anchor.strip()

    def build_persona_messages(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        system_prompt: Optional[str] = None,
        identity_mode: str = "okabe",
    ) -> list[dict]:
        """Build the canonical persona/history envelope shared by all providers."""
        messages = [
            {
                "role": "system",
                "content": self.get_anchored_system_prompt(
                    system_prompt, identity_mode=identity_mode
                ),
            }
        ]
        messages.extend(self._fewshot)
        if memory_summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"これまでの会話の記憶要約：{memory_summary}",
                }
            )
        messages.extend(active_history)
        return messages

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
        """
        Assemble messages and yield chunks from DeepSeek Chat Completion API.
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured.")

        messages = build_stream_messages(
            self.build_persona_messages,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        
        effective_model = model or self.model
        payload = build_chat_payload(
            model=effective_model,
            messages=messages,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        
        sys_content = messages[0]["content"] if messages else ""
        has_emo = "[EMO:" in sys_content
        print(
            "[DeepSeek] request prepared "
            f"messages={len(messages)} tools={tools is not None} "
            f"emotion_contract={has_emo} system_chars={len(sys_content)}",
            flush=True,
        )
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=120.0, write=10.0, pool=5.0)) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderRequestError("deepseek", response.status_code)
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                            if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                                delta = chunk_data["choices"][0].get("delta", {})
                                content = delta.get("content")
                                tool_calls = delta.get("tool_calls")
                                if content or tool_calls:
                                    yield {
                                        "content": content,
                                        "tool_calls": tool_calls
                                    }
                        except json.JSONDecodeError:
                            continue

    async def compress_history(
        self,
        history: list,
        api_key: Optional[str] = None,
        old_summary: str = "",
        model: Optional[str] = None,
        identity_mode: str = "okabe",
    ) -> str:
        """
        Asynchronously call DeepSeek to compress older history into ONE compact
        Japanese working-continuity line (D38 short-term contract).
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            print("[DeepSeek] API Key not configured; skipping history compression.")
            return ""

        from app.security.prompt import compression_system_prompt

        conversation_text = ""
        for msg in history:
            role = "ユーザー" if msg["role"] == "user" else "紅莉栖"
            conversation_text += f"{role}: {msg['content']}\n"

        system_prompt = compression_system_prompt(identity_mode)

        prompt = ""
        if old_summary:
            prompt += f"【これまでの作業コンテキスト】\n{old_summary}\n\n"
        prompt += f"【追加の会話履歴】\n{conversation_text}"
        
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3
        }
        
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"[DeepSeek] Failed to compress history: {e}")
                return ""

    async def translate_to_zh(self, text: str, api_key: Optional[str] = None, model: Optional[str] = None) -> str:
        """
        Translate Japanese response (without emo tag) to Chinese.
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            print("[DeepSeek] API Key not configured; skipping translation.", flush=True)
            return ""

        if not text.strip():
            return ""

        from app.domain.subtitle_glossary_prompt import build_translation_messages

        # Q87 Slice B PROMPT-ENVELOPE: system (guards+glossary) + user (<source>).
        messages = build_translation_messages(text)

        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": 0.3
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                print(f"[DeepSeek] Translation failed: {e}", flush=True)
                raise ConnectionError("translation_unavailable") from e
            except httpx.HTTPStatusError as e:
                print(f"[DeepSeek] Translation failed: {e}", flush=True)
                raise DeepSeekTranslationError(e.response.status_code) from e
            except Exception as e:
                print(f"[DeepSeek] Translation failed: {e}", flush=True)
                raise RuntimeError("translation_unavailable") from e

    async def complete_json(
        self,
        messages: list[dict],
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> dict:
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            raise ValueError("DeepSeek credential is not configured")
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("provider structured output was not a JSON object")
        return result

    async def list_models(
        self,
        api_key: Optional[str] = None,
    ) -> list[str | dict]:
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            raise ValueError("DeepSeek credential is not configured")
        headers = {"Authorization": f"Bearer {effective_api_key}"}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
        ) as client:
            response = await client.get(f"{self.base_url}/models", headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"DeepSeek model discovery failed with status {response.status_code}"
            )
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [
            row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
