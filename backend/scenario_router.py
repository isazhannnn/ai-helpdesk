"""LLM routing for the Saqta Insurance voice-router simulation."""

import json
import os
from pathlib import Path
from time import perf_counter

from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError, OpenAI

CATALOG_PATH = Path(__file__).parent / "knowledge" / "scenarios.json"
with CATALOG_PATH.open(encoding="utf-8") as source:
    CATALOG = json.load(source)
SCENARIOS = {item["scenario_id"]: item for item in CATALOG["scenarios"]}


def _routing_catalog() -> str:
    """Compact source of truth sent to the router; responses stay server-side."""
    items = []
    for scenario in SCENARIOS.values():
        items.append({
            "id": scenario["scenario_id"],
            "name": scenario["name"],
            "description": scenario["description"],
            "not_this_if": scenario.get("not_this_if", []),
            "required_slots": scenario["slots"]["required"],
            "examples": {"ru": scenario["examples"].get("ru", [])[:1], "kk": scenario["examples"].get("kk", [])[:1]},
        })
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


ROUTING_CATALOG = _routing_catalog()
ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "scenario_id": {"type": "string", "enum": list(SCENARIOS)},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
        "language": {"type": "string", "enum": ["ru", "kk", "mixed"]},
        "reply_language": {"type": "string", "enum": ["ru", "kk", "mixed"]},
        "alternative_ids": {"type": "array", "items": {"type": "string", "enum": list(SCENARIOS)}, "minItems": 0, "maxItems": 3},
        "missing_slots": {"type": "array", "items": {"type": "string"}},
        "topic_switched": {"type": "boolean"},
    },
    "required": ["scenario_id", "confidence", "reason", "language", "reply_language", "alternative_ids", "missing_slots", "topic_switched"],
    "additionalProperties": False,
}


def route_conversation(history: list[dict[str, str]]) -> tuple[dict[str, object], int]:
    """Return a validated scenario decision and measured routing latency."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI routing is not configured. Add OPENAI_API_KEY to .env.")
    prompt = "\n".join(f"{item['role']}: {item['content']}" for item in history[-10:])
    instructions = f"""You are the routing layer for Saqta Insurance contact center.
Select exactly one scenario from the catalog for the customer's CURRENT request. The conversation may mix Russian and Kazakh.
Use dialogue context, but switch scenarios when the customer changes topic. Carefully apply not_this_if boundaries.
Return JSON only following the supplied schema. Confidence is your calibrated 0-100 estimate; alternatives must be plausible competing scenarios.
Set language and reply_language from the customer's latest message, not just its final word or sentence. Use ru for Russian-only requests, kk for Kazakh-only requests, and mixed when the request contains both languages. A mixed request must receive a natural mixed-language reply.
SCENARIO CATALOG: {ROUTING_CATALOG}"""
    started = perf_counter()
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            instructions=instructions,
            input=prompt,
            text={"format": {"type": "json_schema", "name": "saqta_route", "strict": True, "schema": ROUTE_SCHEMA}},
            store=False,
        )
        decision = json.loads(response.output_text)
    except APIConnectionError as error:
        raise HTTPException(status_code=503, detail="Unable to reach the AI routing service. Please try again shortly.") from error
    except APIStatusError as error:
        raise HTTPException(status_code=502, detail="The AI routing service could not classify this request. Please try again.") from error
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=502, detail="The AI routing service returned an invalid route.") from error
    return decision, round((perf_counter() - started) * 1000)


def selected_scenario(scenario_id: str) -> dict[str, object]:
    return SCENARIOS[scenario_id]
