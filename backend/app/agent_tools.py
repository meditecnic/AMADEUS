# backend/app/agent_tools.py

RECALL_MEMORY_TOOL_NAME = "recall_memory"

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Perform a web search to retrieve real-time or missing information when needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to retrieve information for."
                }
            },
            "required": ["query"]
        }
    }
}

# Read gate shared by the system content and the tool description. The decision
# remains with the model; the rule makes the required order and personal scope clear.
MEMORY_RECALL_DECISION_RULE = (
    "[MEMORY READ GATE]\n"
    "Decide whether memory is needed before writing any reply. Call recall_memory "
    "when the reply depends on stable personal information the user shared in "
    "earlier conversations and that answer is not reliably present in the active "
    "conversation. This includes indirect questions about the user's own habits, "
    "preferences, restrictions, relationships, possessions, or pets—for example, "
    "their current morning drink, food they avoid, or what kind of animal their "
    "'little one' is. If recall is needed, the first response must contain only the "
    "recall_memory tool call. Do not emit [EMO:], advice, a clarification, or other "
    "text before that call. Split a multi-fact question into one to three standalone, "
    "atomic needs. Read first before guessing, saying you do not know, claiming there "
    "is no record, judging that you cannot tell, or asking the user to repeat known "
    "personal information. Do not read for general knowledge, current public "
    "information, operational instructions, translation of quoted text, or an answer "
    "already reliably available in the active conversation."
)

# The same category-level requirements, written for the tool-schema surface.
RECALL_MEMORY_TOOL_DESCRIPTION = (
    "Read a small set of stored user facts before any visible reply when the answer "
    "depends on stable personal information learned in earlier conversations and is "
    "not reliably available in the active conversation. This includes indirect "
    "questions about the user's habits, preferences, restrictions, relationships, "
    "possessions, or pets. When this tool is needed, call it before emitting [EMO:], "
    "advice, clarification, or other text. Submit one to three standalone, atomic "
    "information needs for a multi-fact question. Read before guessing, saying you do "
    "not know, claiming there is no record, or judging that you cannot tell. Do not "
    "browse or enumerate the Memory store. Do not use for general knowledge, current "
    "public information, operational instructions, quoted-text translation, or an "
    "answer already reliably available in the active conversation."
)

RECALL_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": RECALL_MEMORY_TOOL_NAME,
        "description": RECALL_MEMORY_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "needs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                }
            },
            "required": ["needs"],
        },
    },
}

MEMORY_ONLY_TOOLS = [RECALL_MEMORY_TOOL]
MEMORY_WEB_TOOLS = [RECALL_MEMORY_TOOL, WEB_SEARCH_TOOL]
AMADEUS_TOOLS = [WEB_SEARCH_TOOL]

tools = AMADEUS_TOOLS


def tools_include_recall_memory(tools) -> bool:
    """True when this exact request's tool list actually offers recall_memory."""

    for tool in tools or ():
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name") == RECALL_MEMORY_TOOL_NAME:
            return True
    return False
