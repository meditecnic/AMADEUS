# backend/app/agent_tools.py

AMADEUS_TOOLS = [
    {
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
]

tools = AMADEUS_TOOLS
