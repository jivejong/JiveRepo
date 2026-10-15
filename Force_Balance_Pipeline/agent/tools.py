"""The five function declarations the model sees (doc 06 "Tools").

The three information tools return data that is already in the user message (doc 06 "Runtime", context preload); they stay
declared so the model can use the call pattern it expects. deploy and stand_down are the two terminal calls.
"""

MAX_RATIONALE_CHARS = 1000
MAX_PARTY = 4

FUNCTION_DECLARATIONS = [
    {
        "name": "get_disturbance_context",
        "description": "Returns the disturbance context, which is already in the user message.",
        "parametersJsonSchema": {
            "type": "object",
            "properties": {"disturbance_id": {"type": "string"}},
            "required": ["disturbance_id"],
        },
    },
    {
        "name": "list_available_jedi",
        "description": "Returns the Jedi with no open deployment, which is already in the user message.",
        "parametersJsonSchema": {
            "type": "object",
            "properties": {"sector_id": {"type": "string"}, "signature": {"type": "string"}},
            "required": ["sector_id", "signature"],
        },
    },
    {
        "name": "list_available_starships",
        "description": "Returns the starships with no open deployment, which is already in the user message.",
        "parametersJsonSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "deploy",
        "description": "Deploys one to four available Jedi on one available starship; ends the assessment.",
        "parametersJsonSchema": {
            "type": "object",
            "properties": {
                "jedi_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PARTY},
                "starship_id": {"type": "string"},
                "rationale": {"type": "string", "maxLength": MAX_RATIONALE_CHARS},
            },
            "required": ["jedi_ids", "starship_id", "rationale"],
        },
    },
    {
        "name": "stand_down",
        "description": "Decides that no deployment is warranted; ends the assessment.",
        "parametersJsonSchema": {
            "type": "object",
            "properties": {"rationale": {"type": "string", "maxLength": MAX_RATIONALE_CHARS}},
            "required": ["rationale"],
        },
    },
]
