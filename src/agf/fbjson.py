import json

_GUARD = "for (;;);"


def parse_fb_json(text: str) -> dict:
    """Strip Facebook's anti-hijack guard prefix, then parse JSON."""
    stripped = text.lstrip()
    if stripped.startswith(_GUARD):
        stripped = stripped[len(_GUARD):]
    return json.loads(stripped)
