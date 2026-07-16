import json
import httpx
from typing import Optional
from app import config
from app.services.llm_interface import LLMInterface
from app.services.persona_bible import persona_bible


class DeepSeekTranslationError(RuntimeError):
    """Translation request failure with a retry-relevant HTTP status only."""

    def __init__(self, status_code: int | None = None) -> None:
        super().__init__("translation_unavailable")
        self.status_code = status_code


class DeepSeekService(LLMInterface):
    def __init__(self):
        self.api_key = ""
        self.model = config.DEEPSEEK_MODEL  # defaults to deepseek-v4-flash
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

    def _load_anchor_clause(self) -> str:
        return persona_bible.render_provider_anchor()

    def get_anchored_system_prompt(self, base_prompt: str = None) -> str:
        prompt = base_prompt if base_prompt is not None else self._system_prompt
        if "PERSONA BIBLE v1 (canonical runtime persona)" in prompt:
            anchor = persona_bible.render_output_anchor()
        else:
            anchor = self._load_anchor_clause()
        return prompt.strip() + "\n" + anchor.strip()

    def build_persona_messages(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> list[dict]:
        """Build the canonical persona/history envelope shared by all providers."""
        messages = [
            {
                "role": "system",
                "content": self.get_anchored_system_prompt(system_prompt),
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
        reasoning_effort: Optional[str] = None
    ):
        """
        Assemble messages and yield chunks from DeepSeek Chat Completion API.
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured.")

        eff_system = system_prompt
        if reasoning_effort and (model or self.model) != "deepseek-v4-pro":
            depth_map = {"high": "深", "xhigh": "極深"}
            depth_zh = depth_map.get(reasoning_effort, "中")
            eff_system = (system_prompt or "") + f"\n【システム内部設定】思考深度：{depth_zh}\n（これは内部設定タグです。応答でこの形式を模倣しないでください。必ず [EMO:xxx] から始めてください。）"
        messages = self.build_persona_messages(
            active_history,
            memory_summary,
            eff_system,
        )
        
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature if temperature is not None else 0.7
        }
        
        effective_model = model or self.model
        if effective_model == "deepseek-v4-pro":
            payload["thinking"] = {"type": "enabled"}
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
        
        sys_content = messages[0]["content"] if messages else ""
        has_emo = "[EMO:" in sys_content
        print(
            "[DeepSeek] request prepared "
            f"messages={len(messages)} tools={tools is not None} "
            f"emotion_contract={has_emo} system_chars={len(sys_content)}",
            flush=True,
        )
        
        if tools is not None:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    err_content = await response.aread()
                    raise RuntimeError(f"DeepSeek API error ({response.status_code}): {err_content.decode('utf-8')}")
                
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

    async def compress_history(self, history: list, api_key: Optional[str] = None, old_summary: str = "", model: Optional[str] = None) -> str:
        """
        Asynchronously call DeepSeek to compress older history into a single Japanese summary sentence.
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            print("[DeepSeek] API Key not configured; skipping history compression.")
            return ""

        from app.security.prompt import COMPRESSION_PROMPT_SUFFIX

        conversation_text = ""
        for msg in history:
            role = "ユーザー" if msg["role"] == "user" else "紅莉栖"
            conversation_text += f"{role}: {msg['content']}\n"
            
        system_prompt = (
            "これまでの会話内容を、牧瀬紅莉栖の視点から一行の日本語の『記憶の要約』として簡潔にまとめてください。"
            "余計な解説や前置きは一切含めず、要約された一行の日本語テキストのみを出力してください。\n"
            + COMPRESSION_PROMPT_SUFFIX
        )
        
        prompt = ""
        if old_summary:
            prompt += f"【これまでの記憶要約】\n{old_summary}\n\n"
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

        prompt = (
            "あなたは優秀な翻訳家です。以下の日本語のセリフを、感情を込めた自然な中国語に翻訳してください。\n"
            "【翻訳の制約】\n"
            "- 不自然な畳語（叠字、例：「好、好、好……」）や過度な繰り返し、吃音表現を生成しないこと。\n"
            "- 余計な解説や前置きは一切含めず、翻訳後の中国語テキストのみを出力してください。\n\n"
            f"【翻訳対象】\n{text}"
        )
        
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
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

    async def list_models(self, api_key: Optional[str] = None) -> list[str]:
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
            row["id"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
