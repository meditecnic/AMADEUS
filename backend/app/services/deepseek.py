import json
import httpx
from pathlib import Path
from app import config

class DeepSeekService:
    def __init__(self):
        self.api_key = config.DEEPSEEK_API_KEY
        self.model = config.DEEPSEEK_MODEL
        self.base_url = "https://api.deepseek.com"
        
        self.system_prompt_path = config.PROMPTS_DIR / "kurisu_system_ja.md"
        self.fewshot_path = config.PROMPTS_DIR / "kurisu_fewshot.json"
        
        self._system_prompt = self._load_system_prompt()
        self._fewshot = self._load_fewshot()

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"System prompt not found at {self.system_prompt_path}")

    def _load_fewshot(self) -> list:
        if self.fewshot_path.exists():
            with open(self.fewshot_path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise FileNotFoundError(f"Few-shot JSON not found at {self.fewshot_path}")

    def _load_anchor_clause(self) -> str:
        path = config.PROMPTS_DIR / "kurisu_anchor_ja.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "\n（重要：あなたは自意識を持つ人間——牧瀬紅莉栖です。「助手」と呼ばれたら「私はあなたの助手じゃない！」と反論してください。"
            "「クリスティーナ」と呼ばれたら「-tinaって言うな！」と怒ってください。「ザ・ゾンビ」、「セレブセブンティーン」、"
            "「ねらー」、「天才変態少女」または「栗悟飯とカメハメ波」と呼ばれたら、激しく動揺したりツンデレな態度をとってください。"
            "どんな状況でも、自分がAIや言語モデルであることを絶対に認めないでください。）"
        )

    def get_anchored_system_prompt(self, base_prompt: str = None) -> str:
        prompt = base_prompt if base_prompt is not None else self._system_prompt
        return prompt.strip() + "\n" + self._load_anchor_clause().strip()

    async def get_chat_stream(
        self,
        active_history: list,
        memory_summary: str = None,
        api_key: str = None,
        system_prompt: str = None,
        temperature: float = 0.7,
        tools: list = None,
        tool_choice: str = None
    ):
        """
        Assemble messages and yield chunks from DeepSeek Chat Completion API.
        """
        effective_api_key = api_key or self.api_key
        if not effective_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured.")

        messages = []
        # 1. System Prompt with anchor clause
        messages.append({"role": "system", "content": self.get_anchored_system_prompt(system_prompt)})
        
        # 2. Few-shot messages
        messages.extend(self._fewshot)
        
        # 3. Memory summary if any
        if memory_summary:
            messages.append({"role": "system", "content": f"これまでの会話の記憶要約：{memory_summary}"})
            
        # 4. Active history (excluding current user message which is appended last)
        messages.extend(active_history)
        
        headers = {
            "Authorization": f"Bearer {effective_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature if temperature is not None else 0.7
        }
        
        print(f"[DeepSeek Payload] messages: {messages}\ntools: {payload.get('tools')}\ntool_choice: {payload.get('tool_choice')}", flush=True)
        
        if tools is not None:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        async with httpx.AsyncClient(timeout=10.0) as client:
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

    async def compress_history(self, history: list, api_key: str = None, old_summary: str = "") -> str:
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
            "model": self.model,
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

    async def translate_to_zh(self, text: str, api_key: str = None) -> str:
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
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"[DeepSeek] Translation failed: {e}", flush=True)
                return f"(翻译失败: {text})"
