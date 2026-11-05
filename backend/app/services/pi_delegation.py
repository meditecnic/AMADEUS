"""Small chat wiring for the developer-only pi tool."""

from __future__ import annotations

import json

from app import config
from app.agent_tools import DELEGATE_TASK_TOOL, DELEGATE_TASK_TOOL_NAME
from app.services.deepseek import DeepSeekService
from app.services.pi_agent import PiAgentError, PiSupervisor
from app.services.provider_adapters import OpenAICompatibleAdapter


def available(provider) -> bool:
    return bool(
        config.PI_AGENT_ENABLED
        and provider.capabilities.tools
        and isinstance(provider.adapter, (DeepSeekService, OpenAICompatibleAdapter))
    )


def offer_tools(tools, provider):
    if not available(provider):
        return tools
    return [*(tools or []), DELEGATE_TASK_TOOL]


async def execute_delegations(calls, provider, api_key, *, cancelled=False):
    if not available(provider):
        return []
    from app.services.turn_tool_orchestrator import validate_tool_batch

    invalid = cancelled or validate_tool_batch(calls)
    results = []
    for call in calls:
        function = call.get("function") or {}
        if function.get("name") != DELEGATE_TASK_TOOL_NAME:
            continue
        text = "Delegation unavailable."
        if not invalid:
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict) or set(args) != {"task", "folder"}:
                    raise ValueError("invalid delegation arguments")
                task, folder = args["task"], args["folder"]
                if (not isinstance(task, str) or not task.strip() or len(task) > 8000
                        or not isinstance(folder, str) or not folder.strip() or len(folder) > 4096):
                    raise ValueError("invalid delegation arguments")
                text = await PiSupervisor().run(
                    task, folder, base_url=provider.adapter.base_url,
                    model=provider.model_id, api_key=api_key,
                )
            except (PiAgentError, ValueError, TypeError, OSError):
                pass
        results.append((DELEGATE_TASK_TOOL_NAME, text))
    return results
