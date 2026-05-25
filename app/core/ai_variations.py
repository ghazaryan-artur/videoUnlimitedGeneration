"""AI-powered prompt variation generator using Claude API.

Takes a source video prompt and produces N variations that swap out the
visual surface details (character identities, wardrobe, decor) while
preserving the core scene structure and narrative beats.

Uses prompt caching for the system prompt — the second variation request
in a 5-minute window pays ~10% of the system-prompt token cost.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.paths import user_data_root


# --- FROZEN system prompt — never edit without bumping the cache key ---
# Any byte change here invalidates the prompt cache for everyone.

SYSTEM_PROMPT = """You are an expert prompt engineer for AI video-generation models like Runway Gen-3, Seedance, and Kling.

Your job: take a SOURCE VIDEO PROMPT and produce N varied prompts that swap visual surface details while preserving the core scene structure, narrative beats, and emotional content.

# WHAT TO PRESERVE
- Scene structure: who is doing what, when, and where in the frame
- Camera work: angles, motion, framing, beat-by-beat blocking
- Lighting and atmosphere: time of day, mood, color palette
- Action sequence: order of events, emotional tone, key moments
- Setting *type*: a classroom stays a classroom; a kitchen stays a kitchen

# WHAT TO VARY (default — adjust per any user instructions)
- Character physical identity: ethnicity, age range, build, hair style/color, facial features
- Wardrobe: clothing styles, colors, fabrics, accessories
- Minor incidental props that don't carry the scene
- Specific decor/location details *within the same setting type*

# RULES
- Each variation reads like a DIFFERENT CAST performing the SAME SCENE.
- Match the prose style and detail level of the source. If the source uses headers (SETTING, CHARACTERS, ONE-SHOT SCENE), keep them.
- If the source names characters (Mark, Lily, Ethan), keep those names; vary their physical descriptions.
- Do NOT introduce new characters that weren't in the source.
- Do NOT change the duration, aspect ratio, or any meta-instructions.
- Each variation must be MEANINGFULLY distinct from the others — no near-duplicates.
- No commentary, no preamble, no markdown around the output. Output strictly the JSON shape requested."""


@dataclass(frozen=True)
class VariationRequest:
    source_prompt: str
    count: int
    custom_instructions: str | None = None  # extra user guidance, optional


@dataclass(frozen=True)
class VariationResult:
    variations: list[str]
    cache_hit: bool
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


# --- API key storage ---


def _key_file() -> Path:
    return user_data_root() / "anthropic_api_key.txt"


def get_stored_api_key() -> str | None:
    f = _key_file()
    if not f.exists():
        return None
    try:
        v = f.read_text(encoding="utf-8").strip()
        return v or None
    except OSError:
        return None


def store_api_key(api_key: str) -> None:
    _key_file().write_text(api_key.strip(), encoding="utf-8")


def clear_api_key() -> None:
    f = _key_file()
    if f.exists():
        f.unlink(missing_ok=True)


# --- Generation ---


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "variations": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
    },
    "required": ["variations"],
    "additionalProperties": False,
}


async def generate_variations(
    request: VariationRequest,
    *,
    api_key: str | None = None,
) -> VariationResult:
    """Call Claude API to produce variations. Lazy-imports anthropic."""
    import anthropic  # heavy — keep lazy

    if api_key is None:
        api_key = get_stored_api_key()
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key configured. Save one in the app's AI Variations dialog."
        )

    user_msg = f"Generate {request.count} variation"
    user_msg += "" if request.count == 1 else "s"
    user_msg += " of this video prompt.\n\n"
    if request.custom_instructions and request.custom_instructions.strip():
        user_msg += "ADDITIONAL INSTRUCTIONS FROM THE USER:\n"
        user_msg += request.custom_instructions.strip() + "\n\n"
    user_msg += "SOURCE PROMPT:\n" + request.source_prompt

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model="claude-opus-4-7",
            max_tokens=8000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {
                    "type": "json_schema",
                    "schema": _OUTPUT_SCHEMA,
                },
            },
        )
    finally:
        await client.close()

    text_block_text = next(
        (b.text for b in response.content if b.type == "text"),
        "",
    )
    if not text_block_text:
        raise RuntimeError("Claude returned no text content.")

    try:
        data = json.loads(text_block_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned non-JSON: {text_block_text[:300]}") from e

    variations_raw = data.get("variations")
    if not isinstance(variations_raw, list):
        raise RuntimeError("Response missing 'variations' array")

    variations = [v for v in variations_raw if isinstance(v, str) and v.strip()]
    if not variations:
        raise RuntimeError("Claude returned an empty variations list.")

    usage = response.usage
    return VariationResult(
        variations=variations,
        cache_hit=(usage.cache_read_input_tokens or 0) > 0,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_tokens=usage.cache_creation_input_tokens or 0,
    )
