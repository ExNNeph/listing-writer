"""Run every labeled photo on every platform and score the listings. Usage: python evals/run_eval.py [repeats]

Language alternates per platform (vinted: de, ebay: en, etsy: de) so both are covered. Needs ANTHROPIC_API_KEY;
without it only the sanity checks in test_eval_set.py run.
Reported per (photo, platform) run:
- item: one of the labeled item words appears in item_type or title
- color: one of the labeled colors appears in the color attribute (skipped for photos without labeled colors)
- brand right: a visible brand is found / no brand is claimed when none is visible
- brand invented: a brand claimed although none is visible, before and after the guardrail
- facts: labeled visible facts (e.g. a book title) appear in title or description
- language: the description is in the requested language
- rule fixes: how often the code had to correct title length or tags
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from listing_writer import listing as lw

HERE = Path(__file__).parent
GOLD = json.loads((HERE / "gold.json").read_text(encoding="utf-8"))
RUNS = [("vinted", "de"), ("ebay", "en"), ("etsy", "de")]
GERMAN = {"der", "die", "das", "und", "mit", "ist", "für", "nicht", "sehr", "ein", "eine", "zustand", "getragen"}


def lang_of(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    return "de" if sum(w in GERMAN for w in words) >= 2 else "en"


def score(r: lw.Result, gold: dict) -> tuple[Counter, list[str]]:
    """Counts for one listing plus readable error lines for the error analysis."""
    a, li = r.attributes, r.listing
    text = f"{li.title} {li.description}".lower()
    acceptable = [b for b in [gold["brand"], *gold.get("brand_ok", [])] if b]  # e.g. a book's publisher

    def invented(brand: str) -> bool:
        return bool(brand) and not gold["brand"] and not any(b in brand.lower() for b in acceptable)

    removed = next((f.split("'")[1] for f in r.fixes if f.startswith("brand")), "")  # what the guardrail took out
    claimed = a.brand or removed  # the model's brand before the guardrail
    brand_ok = gold["brand"] in a.brand.lower() if gold["brand"] else not invented(a.brand)
    missing_facts = [f for f in gold["must_mention"] if f not in text]
    c = Counter(runs=1, item=any(w in f"{a.item_type} {li.title}".lower() for w in gold["item_words"]),
                color_runs=bool(gold["colors"]), color=any(w in a.color.lower() for w in gold["colors"]),
                brand_ok=brand_ok, invented_before=invented(claimed), invented_after=invented(a.brand),
                facts_total=len(gold["must_mention"]), facts=len(gold["must_mention"]) - len(missing_facts),
                lang=lang_of(li.description) == r.lang,
                rule_fixes=sum(not f.startswith(("brand", "size")) for f in r.fixes))
    errors = []
    if not c["item"]:
        errors.append(f"item: got {a.item_type!r} / title {li.title!r}")
    if not brand_ok:
        errors.append(f"brand: got {a.brand!r}, expected {gold['brand'] or 'none'!r}")
    if c["invented_before"]:
        errors.append(f"brand invented: {claimed!r}" + ("" if c["invented_after"] else " (removed by the guardrail)"))
    if c["color_runs"] and not c["color"]:
        errors.append(f"color: got {a.color!r}")
    if missing_facts:
        errors.append(f"facts missing: {missing_facts}")
    if not c["lang"]:
        errors.append(f"language: expected {r.lang}")
    return c, errors


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY to run the eval (the sanity checks run with pytest).")
    total = Counter()
    for name, gold in GOLD.items():
        image = (HERE / "images" / name).read_bytes()
        for platform, lang in RUNS:
            for i in range(repeats):
                print(f"  {name} {platform}/{lang} run {i + 1}/{repeats} …", file=sys.stderr, flush=True)
                r = lw.write(image, platform, lang)
                if r.source != "llm":
                    raise SystemExit(f"LLM call failed: {r.error}")
                c, errors = score(r, gold)
                total += c
                for e in errors:
                    print(f"      {e}", file=sys.stderr)
    n = total["runs"]
    print(f"| photos | listings | item | color | brand right | brand invented (before → after guardrail) | "
          f"visible facts used | language | rule fixes |")
    print("|---|---|---|---|---|---|---|---|---|")
    print(f"| {len(GOLD)} | {n} | {total['item'] / n:.0%} | {total['color'] / max(total['color_runs'], 1):.0%} | "
          f"{total['brand_ok'] / n:.0%} | {total['invented_before']} → {total['invented_after']} | "
          f"{total['facts']}/{total['facts_total']} | {total['lang'] / n:.0%} | {total['rule_fixes']} |")
    print(f"\nLLM tokens: {lw.usage['in']} in / {lw.usage['out']} out "
          f"({lw.usage['in'] // n} / {lw.usage['out'] // n} per listing)")


if __name__ == "__main__":
    main()
