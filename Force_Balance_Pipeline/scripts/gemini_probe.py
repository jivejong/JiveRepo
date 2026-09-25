#!/usr/bin/env python3
"""Bisect a Gemini 400 INVALID_ARGUMENT by adding one request feature at a time.

When enrich_planets.py or enrich_jedi.py fails with "Request contains an invalid argument" and no
field name, this sends a ladder of small generateContent calls, each adding or removing one
feature, and prints which steps Google rejects. Steps that use the real prompts and schemas cap
the output at 64 tokens (a truncated reply is still an accepted request), so the ladder is cheap.

It makes real API calls (one per step that is not skipped). Nothing is written to disk. The API
key is read from GEMINI_API_KEY (or .env) and is never printed.

Step numbers and their requests are stable across versions. Steps 7 and 13 tested the responseFormat
request shape, which Google rejected with an error naming the field; they are skipped and kept only
so the numbering matches earlier results. Steps 11, 12, 14, 16 and 18 add exact minItems/maxItems
back explicitly, because the real schemas no longer carry them (Google rejects them); steps 15 and
17 are the requests the enrich scripts now send.

Usage:
    python scripts/gemini_probe.py --thinking-level low
    python scripts/gemini_probe.py --thinking-level low --only 12,15,16,17,18
"""
import argparse
import copy
import json
import sys

import enrich_common as ec
import enrich_jedi
import enrich_planets as ep
import gemini_client
import jedi_roster

PROMPT = "Reply with a short JSON object."
TRIVIAL_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
RICH_SCHEMA = {  # the keyword families the real schemas use, on a tiny shape
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["a", "b", "c"]},
        "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "count": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["kind", "score", "count"],
    "additionalProperties": False,
}
ARRAY_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": RICH_SCHEMA, "minItems": 3, "maxItems": 3}},
    "required": ["items"],
    "additionalProperties": False,
}
NULLABLE_SCHEMA = {
    "type": "object",
    "properties": {"note": {"type": ["string", "null"]}},
    "required": ["note"],
    "additionalProperties": False,
}
CANDIDATE_CAPS = (32768, 65536)  # maxOutputTokens values a real run might use (steps 19, 20)


def base():
    return {"contents": [{"role": "user", "parts": [{"text": PROMPT}]}]}


def with_config(body, **config):
    body = copy.deepcopy(body)
    body.setdefault("generationConfig", {}).update(config)
    return body


def structured(body, schema):
    return with_config(body, responseMimeType="application/json", responseJsonSchema=schema)


def cap(body, tokens=64):
    return with_config(body, maxOutputTokens=tokens)


def add_lengths(n):
    """Re-add exact minItems/maxItems, which the real schemas no longer carry (Google rejects them)."""
    def _add(array_node):
        array_node["minItems"] = n
        array_node["maxItems"] = n
    return _add


def drop_id_enum(id_key):
    def _drop(array_node):
        array_node["items"]["properties"][id_key].pop("enum", None)
    return _drop


def chain(*mutators):
    def _run(array_node):
        for m in mutators:
            m(array_node)
    return _run


def real_planet_body(level, n, mutate=None):
    planets = ec.read_snapshot("planets")
    sid_map = ec.sector_ids(planets)
    to_enrich = sorted((p for p in planets if ec.url_id(p["url"]) != ec.UNKNOWN_PLANET_ID),
                       key=lambda p: ec.url_id(p["url"]))[:n]
    ids = [sid_map[ec.url_id(p["url"])] for p in to_enrich]
    schema = ep.batch_schema(ids)
    if mutate:
        mutate(schema["properties"]["planets"])
    return cap(gemini_client.build_request(ep.SYSTEM_PROMPT, ep.user_prompt(to_enrich, sid_map, False),
                                           schema, level))


def real_jedi_body(level, mutate=None):
    planets = ec.read_snapshot("planets")
    records, _ = jedi_roster.verify(ec.read_snapshot("people"))
    inputs = enrich_jedi.build_inputs(records, planets, ec.read_snapshot("species"), ec.sector_ids(planets))
    schema = enrich_jedi.batch_schema([i["jedi_id"] for i in inputs])
    if mutate:
        mutate(schema["properties"]["jedi"])
    return cap(gemini_client.build_request(enrich_jedi.SYSTEM_PROMPT, enrich_jedi.user_prompt(inputs),
                                           schema, level))


def ladder(level):
    """(number, label, body) per step; body is None for a skipped step (its label says why)."""
    sysb = base()
    sysb["systemInstruction"] = {"parts": [{"text": "You are terse."}]}
    skipped_rf = "SKIPPED: responseFormat was rejected with a named field error (evidence)"
    return [
        (1, "contents only (key, model name, endpoint)", cap(base())),
        (2, "+ systemInstruction", cap(sysb)),
        (3, f"+ thinkingLevel={level}", cap(with_config(sysb, thinkingConfig={"thinkingLevel": level}))),
        (4, "+ thinkingLevel=minimal", cap(with_config(sysb, thinkingConfig={"thinkingLevel": "minimal"}))),
        (5, "+ responseMimeType only", cap(with_config(sysb, responseMimeType="application/json"))),
        (6, "responseMimeType + responseJsonSchema, trivial schema", cap(structured(sysb, TRIVIAL_SCHEMA))),
        (7, f"responseFormat, trivial schema. {skipped_rf}", None),
        (8, "schema with enum + min/max + additionalProperties:false", cap(structured(sysb, RICH_SCHEMA))),
        (9, "schema with an array using minItems/maxItems", cap(structured(sysb, ARRAY_SCHEMA), 256)),
        (10, "schema with a nullable type array", cap(structured(sysb, NULLABLE_SCHEMA))),
        (11, f"real planet request, 3 planets, WITH minItems/maxItems=3 + enum, thinking={level}",
         real_planet_body(level, 3, add_lengths(3))),
        (12, f"real planet request, all 59, WITH minItems/maxItems=59 + enum, thinking={level}",
         real_planet_body(level, 59, add_lengths(59))),
        (13, f"real planet request, all 59, responseFormat. {skipped_rf}", None),
        (14, f"real Jedi request, 17, WITH minItems/maxItems=17 + enum, thinking={level}",
         real_jedi_body(level, add_lengths(17))),
        (15, "59 planets, NO minItems/maxItems, sector_id enum kept (as enrich_planets.py sends now)",
         real_planet_body(level, 59)),
        (16, "59 planets, minItems/maxItems=59 kept, NO sector_id enum",
         real_planet_body(level, 59, chain(add_lengths(59), drop_id_enum("sector_id")))),
        (17, "17 Jedi, NO minItems/maxItems, jedi_id enum kept (as enrich_jedi.py sends now)",
         real_jedi_body(level)),
        (18, "17 Jedi, minItems/maxItems=17 kept, NO jedi_id enum",
         real_jedi_body(level, chain(add_lengths(17), drop_id_enum("jedi_id")))),
    ] + [
        (19 + i, f"cap check: maxOutputTokens={tokens} (trivial request, thinking={level})",
         with_config(sysb, thinkingConfig={"thinkingLevel": level}, maxOutputTokens=tokens))
        for i, tokens in enumerate(CANDIDATE_CAPS)
    ]


def post(api_key, model, body):
    url = f"{gemini_client.API_ROOT}/models/{model}:generateContent"
    status, raw = gemini_client.http_request(
        "POST", url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8"))
    return status, raw.decode("utf-8", "replace")


def summarize(status, text):
    try:
        data = json.loads(text)
    except ValueError:
        return f"non-JSON body: {text[:120]!r}"
    if status == 200:
        cand = (data.get("candidates") or [{}])[0]
        usage = data.get("usageMetadata") or {}
        thoughts = usage.get("thoughtsTokenCount")
        extra = f", thoughtsTokenCount {thoughts}" if thoughts is not None else ""
        return f"accepted (finishReason {cand.get('finishReason')}{extra})"
    err = data.get("error", {})
    details = err.get("details")
    return f"{err.get('status')}: {err.get('message')}" + (f" details={json.dumps(details)[:200]}" if details else "")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--thinking-level", choices=ec.THINKING_LEVELS, default="low")
    p.add_argument("--model", default=ec.ENRICH_MODEL)
    p.add_argument("--only", help="comma-separated step numbers to run, e.g. 12,15,16")
    args = p.parse_args()
    wanted = {int(x) for x in args.only.split(",")} if args.only else None
    steps = [s for s in ladder(args.thinking_level) if wanted is None or s[0] in wanted]
    live = [s for s in steps if s[2] is not None]
    api_key = ec.get_api_key()
    print(f"model {args.model}: {len(live)} API call(s), {len(steps) - len(live)} skipped\n")
    results = []
    for number, label, body in steps:
        if body is None:
            print(f"      skip  {number:<2} {label}")
            continue
        status, text = post(api_key, args.model, body)
        results.append((number, label, status))
        print(f"HTTP {status}  {number:<2} {label}\n              {summarize(status, text)}", flush=True)
    failed = [(n, label) for n, label, status in results if status != 200]
    print(f"\n{len(results) - len(failed)} accepted, {len(failed)} rejected")
    for n, label in failed:
        print(f"  rejected: {n} {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
