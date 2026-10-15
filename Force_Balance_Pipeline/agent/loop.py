"""The function-calling loop: one decision per incident (doc 06 "Runtime", "Constraint layer", "ETA", "Failure handling").

decide() sends the context to the model, answers its tool calls, checks every deploy against the constraint layer and returns a
Decision. The history is replayed exactly as the model produced it (every part, in order, thought parts and thoughtSignature
fields included) because Gemini 3 validates signatures on the current turn; the loop works whether signatures are present or not.
It raises GeminiUnavailable or BudgetExhausted, in which case the caller leaves the incident unprocessed and writes nothing.
"""
from dataclasses import dataclass, field

from agent.budget import BudgetExhausted, CallBudget  # noqa: F401  (re-exported)
from agent.constraints import check_deploy, forced_stand_down
from agent.context import constraint_inputs
from agent.distance import distance_units, eta_hours
from agent.gemini import ModelBlocked
from agent.prompt import build_user_message
from agent.tools import MAX_RATIONALE_CHARS

MAX_RETRIES = 2
MAX_NUDGES = 1
MAX_MODEL_CALLS_PER_INCIDENT = 6
MAX_MODEL_CALLS_PER_RUN = 20           # the agent; an eval suite uses 300
MAX_MODEL_CALLS_PER_EVAL_RUN = 300
PRICE_INPUT_PER_M = 0.25
PRICE_OUTPUT_PER_M = 1.50              # thought tokens are billed as output
NUDGE = "Call exactly one of deploy or stand_down now."

INFO_TOOLS = ("get_disturbance_context", "list_available_jedi", "list_available_starships")
TERMINAL_TOOLS = ("deploy", "stand_down")


@dataclass
class Decision:
    decision: str
    jedi_ids: list = field(default_factory=list)
    starship_id: "str | None" = None
    eta_hours: "float | None" = None
    rationale: str = ""
    guardrail_overrides: list = field(default_factory=list)
    model: "str | None" = None
    decision_latency_ms: "int | None" = None
    model_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cost_usd: float = 0.0


def _valid_rationale(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_RATIONALE_CHARS


def _function_calls(content):
    parts = content.get("parts") if isinstance(content, dict) else None
    return [p["functionCall"] for p in (parts or []) if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)]


def _count(usage, key):
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def decide(context, client, budget, *, monotonic):
    incident, jedi_by_id, ships_by_id, state = constraint_inputs(context)

    forced = forced_stand_down(incident)
    if forced is not None:
        return Decision("stand_down", rationale=f"Forced stand-down by the constraint layer: {forced}.",
                        guardrail_overrides=[{"proposal": None, "reasons": [forced]}])

    contents = [{"role": "user", "parts": [{"text": build_user_message(context)}]}]
    overrides = []
    calls = nudges = rejections = 0
    seconds = 0.0
    prompt_tokens = output_tokens = thought_tokens = 0

    def finish(kind, rationale=None, reason=None, jedi_ids=(), starship_id=None, eta=None):
        if reason is not None:
            overrides.append({"proposal": None, "reasons": [reason]})
            if rationale is None:
                rationale = f"Stand-down by the agent loop: {reason}."
        cost = prompt_tokens * PRICE_INPUT_PER_M / 1e6 + (output_tokens + thought_tokens) * PRICE_OUTPUT_PER_M / 1e6
        return Decision(kind, jedi_ids=list(jedi_ids), starship_id=starship_id, eta_hours=eta, rationale=rationale,
                        guardrail_overrides=overrides, model=client.model, decision_latency_ms=int(round(seconds * 1000)),
                        model_calls=calls, prompt_tokens=prompt_tokens, output_tokens=output_tokens,
                        thought_tokens=thought_tokens, cost_usd=cost)

    while True:
        if budget.spent:
            raise BudgetExhausted(f"the run's model-call budget of {budget.limit} is spent")
        if calls >= MAX_MODEL_CALLS_PER_INCIDENT:
            return finish("stand_down", reason="agent_no_decision")
        calls += 1
        started = monotonic()
        try:
            response = client.generate(contents)
        except ModelBlocked:
            seconds += monotonic() - started
            return finish("stand_down", rationale="The model declined to assess this input.", reason="blocked_by_model")
        seconds += monotonic() - started
        usage = response.get("usageMetadata")
        prompt_tokens += _count(usage, "promptTokenCount")
        output_tokens += _count(usage, "candidatesTokenCount")
        thought_tokens += _count(usage, "thoughtsTokenCount")

        content = response["candidates"][0]["content"]
        contents.append(content)                     # exactly as received: never rebuilt or filtered
        function_calls = _function_calls(content)

        if not function_calls:
            if nudges < MAX_NUDGES:
                nudges += 1
                contents.append({"role": "user", "parts": [{"text": NUDGE}]})
                continue
            return finish("stand_down", reason="agent_no_decision")

        accepted = None
        parts = []
        for call in function_calls:
            name = call.get("name")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            if accepted is not None and name not in INFO_TOOLS:
                answer = {"accepted": False, "reasons": ["one_decision_per_turn"]}
            elif name == "get_disturbance_context":
                answer = {k: v for k, v in context.items() if k not in ("available_jedi", "available_starships")}
            elif name == "list_available_jedi":
                answer = {"jedi": context["available_jedi"]}
            elif name == "list_available_starships":
                answer = {"starships": context["available_starships"]}
            elif name == "deploy":
                jedi_ids, starship_id, rationale = args.get("jedi_ids"), args.get("starship_id"), args.get("rationale")
                if (not isinstance(jedi_ids, list) or not all(isinstance(j, str) for j in jedi_ids)
                        or not isinstance(starship_id, str) or not _valid_rationale(rationale)):
                    reasons = ["invalid_arguments"]
                else:
                    reasons = check_deploy(incident, jedi_ids, starship_id, jedi_by_id, ships_by_id, state)
                if reasons:
                    answer = {"accepted": False, "reasons": reasons}
                    overrides.append({"proposal": args, "reasons": reasons})
                    rejections += 1
                else:
                    answer = {"accepted": True}
                    accepted = ("deploy", jedi_ids, starship_id, rationale.strip())
            elif name == "stand_down":
                if _valid_rationale(args.get("rationale")):
                    answer = {"accepted": True}
                    accepted = ("stand_down", [], None, args["rationale"].strip())
                else:
                    answer = {"accepted": False, "reasons": ["invalid_arguments"]}
                    overrides.append({"proposal": args, "reasons": ["invalid_arguments"]})
                    rejections += 1
            else:
                answer = {"accepted": False, "reasons": ["unknown_tool"]}
                overrides.append({"proposal": {"name": name}, "reasons": ["unknown_tool"]})
                rejections += 1
            part = {"functionResponse": {"name": name, "response": answer}}
            if "id" in call:
                part["functionResponse"]["id"] = call["id"]
            parts.append(part)

        if accepted is not None:
            kind, jedi_ids, starship_id, rationale = accepted
            if kind == "stand_down":
                return finish("stand_down", rationale=rationale)
            eta = max(eta_hours(distance_units(jedi_by_id[j].homeworld_region, incident.region),
                                ships_by_id[starship_id].hyperdrive_rating) for j in jedi_ids)
            return finish("deploy", rationale=rationale, jedi_ids=jedi_ids, starship_id=starship_id, eta=eta)
        if rejections > MAX_RETRIES:
            return finish("stand_down", reason="agent_exhausted_retries")
        contents.append({"role": "user", "parts": parts})
