"""Shared transcript schema; independent of inference libraries."""


def validate_transcript(value: dict, page: int, *, legacy: bool = False) -> dict:
    if not isinstance(value, dict) or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("invalid transcript or page number")
    if value.get("page") != page:
        raise ValueError("transcript page does not match its filename")
    blocks = value.get("blocks")
    if not isinstance(blocks, list) or len(blocks) > 1000:
        raise ValueError("blocks must be an array with at most 1000 entries")
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in {"title", "verse", "prayer", "instruction", "ornament"}:
            raise ValueError("invalid block type")
        if not isinstance(block.get("verse_num_am", ""), str):
            raise ValueError("verse number must be a string")
        if not isinstance(block.get("spans"), list):
            raise ValueError("spans must be an array")
        for span in block["spans"]:
            if not isinstance(span, dict) or not isinstance(span.get("t"), str) or span.get("c") not in {"red", "black"}:
                raise ValueError("invalid text/colour span")
    if not isinstance(value.get("notes", ""), str):
        raise ValueError("notes must be text")
    fields = {"corrections": ("printed", "suggested", "reason"), "uncertain": ("text", "why"), "clipped": ("edge", "note")}
    for name, required in fields.items():
        if legacy and name not in value:
            continue
        if not isinstance(value.get(name), list):
            raise ValueError(f"{name} must be an array")
        for item in value[name]:
            if not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in required):
                raise ValueError(f"invalid {name} entry")
    return value

PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "page": {"type": "integer"},
        "notes": {
            "type": "string",
            "description": (
                "Anything a human proofreader should check on this page: damaged "
                "glyphs, ambiguous colour, text lost to the gutter. Empty if clean."
            ),
        },
        "corrections": {
            "type": "array",
            "description": (
                "Suspected misprints. The spans keep the printed form; these are "
                "suggestions a human accepts or rejects in the review UI."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "printed": {
                        "type": "string",
                        "description": "The word exactly as printed, as it appears in the spans.",
                    },
                    "suggested": {
                        "type": "string",
                        "description": "The standard form you believe was intended.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short plain reason, e.g. 'missing ግ' or 'ሠ should be ሥ'.",
                    },
                },
                "required": ["printed", "suggested", "reason"],
                "additionalProperties": False,
            },
        },
        "uncertain": {
            "type": "array",
            "description": (
                "Words that could not be read confidently. The text must exactly "
                "match the best literal reading placed in a span."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["text", "why"],
                "additionalProperties": False,
            },
        },
        "clipped": {
            "type": "array",
            "description": "Lines whose text is lost beyond a scan edge.",
            "items": {
                "type": "object",
                "properties": {
                    "edge": {"type": "string", "enum": ["top", "bottom"]},
                    "note": {"type": "string"},
                },
                "required": ["edge", "note"],
                "additionalProperties": False,
            },
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["title", "verse", "prayer", "instruction", "ornament"],
                    },
                    "verse_num_am": {
                        "type": "string",
                        "description": "Ethiopic numeral of the verse, or empty string.",
                    },
                    "spans": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "t": {"type": "string"},
                                "c": {"type": "string", "enum": ["black", "red"]},
                            },
                            "required": ["t", "c"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["type", "verse_num_am", "spans"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page", "notes", "corrections", "uncertain", "clipped", "blocks"],
    "additionalProperties": False,
}

