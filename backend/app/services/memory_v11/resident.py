"""Read-only presentation of the chat path's resident CORE FACTS selection."""

from typing import Any

from app.services.memory_v11.recall import MemoryScope, load_resident_fact_candidates


async def load_resident_fact_ids(scope: MemoryScope, *, db: Any | None = None) -> set[str]:
    from app.services.prompt_compiler import prompt_compiler

    candidates = await load_resident_fact_candidates(scope, db=db)
    profile = prompt_compiler.select_resident_profile(candidates, tool_capable=True)
    return {str(fact["fact_id"]) for fact in profile.facts}
