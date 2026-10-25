"""Photo -> attributes -> listing. Claude looks at the photo; the guardrail removes claims the photo and the
seller's own facts do not support, and the platform rules are enforced in code."""
import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageOps
from pydantic import BaseModel

from .platforms import PLATFORMS

log = logging.getLogger(__name__)
usage = {"in": 0, "out": 0}  # running token totals, read by evals/run_eval.py
MODEL = os.getenv("LISTING_WRITER_MODEL", "claude-opus-5-5")
MAX_SIDE = 1568  # longest image side Claude uses without downscaling; larger only costs tokens

SYSTEM = """You write second-hand marketplace listings from one product photo.
First describe what the photo shows, then write the listing for the given platform and language.
Rules:
- brand: only if a brand name or logo is readable in the photo, or the seller gave it. Otherwise "".
- size: only if a size label is readable in the photo, or the seller gave it. Otherwise "".
- visible_text: every word you can actually read on labels, tags or the item itself, exactly as printed.
- Never invent brand, size, material composition, measurements or a model name. If unsure, leave it out.
- condition: one of new, like new, good, fair; mention visible wear in the description.
- Use the seller's facts as true. Write title, description and tags in the requested language."""

FIELDS = {"category": "string", "item_type": "string", "brand": "string", "color": "string", "material": "string",
          "size": "string", "condition": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "attributes": {"type": "object", "additionalProperties": False, "required": [*FIELDS, "visible_text", "defects"],
                       "properties": {**{k: {"type": v} for k, v in FIELDS.items()},
                                      "visible_text": {"type": "array", "items": {"type": "string"}},
                                      "defects": {"type": "array", "items": {"type": "string"}}}},
        "listing": {"type": "object", "additionalProperties": False, "required": ["title", "description", "tags"],
                    "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                                   "tags": {"type": "array", "items": {"type": "string"}}}},
    },
    "required": ["attributes", "listing"],
    "additionalProperties": False,
}


class Attributes(BaseModel):
    category: str = ""
    item_type: str = ""
    brand: str = ""
    color: str = ""
    material: str = ""
    size: str = ""
    condition: str = ""
    visible_text: list[str] = []
    defects: list[str] = []


class Listing(BaseModel):
    title: str
    description: str
    tags: list[str] = []


class Facts(BaseModel):
    """What the seller knows and types in; treated as true."""
    brand: str = ""
    size: str = ""
    notes: str = ""


@dataclass
class Result:
    attributes: Attributes
    listing: Listing
    platform: str
    lang: str
    source: str  # "llm", "template" or "template (API error)"
    fixes: list[str] = field(default_factory=list)  # what the guardrail and platform rules changed
    error: str = ""


def prepare_image(data: bytes) -> tuple[str, str]:
    """Any common image -> (media_type, base64), longest side capped, EXIF rotation applied, metadata dropped."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")  # phone photos are often stored sideways
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)  # re-encoding also strips EXIF data such as GPS position
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


def _mentions(text: str, word: str) -> bool:
    return bool(word) and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE) is not None


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _strip(text: str, word: str) -> str:
    out = re.sub(rf"\s*(?<!\w){re.escape(word)}(?!\w)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" -–|,")


def guard(attrs: Attributes, listing: Listing, facts: Facts) -> tuple[Attributes, Listing, list[str]]:
    """Remove a brand or size that is neither readable in the photo (per visible_text) nor given by the seller.
    ponytail: visible_text is reported by the same model, so this catches inconsistent claims, not a model
    that also invents the label text; the eval measures invented brands against hand labels."""
    seen = " ".join(attrs.visible_text)
    fixes = []
    for name in ("brand", "size"):
        value, given = getattr(attrs, name), getattr(facts, name)
        if value and not _mentions(seen, value) and value.lower() != given.lower():
            fixes.append(f"{name} '{value}' is not readable in the photo and was not given: removed")
            listing = Listing(title=_strip(listing.title, value), description=_strip(listing.description, value),
                              tags=[t for t in listing.tags if _squash(value) not in _squash(t)])  # "levis" too
            attrs = attrs.model_copy(update={name: ""})
    return attrs, listing, fixes


def enforce(listing: Listing, platform: str) -> tuple[Listing, list[str]]:
    """Platform rules in code: title length cut at a word boundary, tag count/length, no duplicate tags."""
    p, fixes = PLATFORMS[platform], []
    title = listing.title.strip()
    if len(title) > p.title_max:
        cut = title[: p.title_max + 1].rsplit(" ", 1)[0].rstrip(" ,-–|")
        fixes.append(f"title shortened from {len(title)} to {len(cut)} characters ({p.name} limit {p.title_max})")
        title = cut
    tags, seen = [], set()
    for tag in (t.strip().lstrip("#").strip() for t in listing.tags):
        if tag and len(tag) <= p.tag_len_max and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    tags = tags[: p.tags_max]  # tags_max 0: the platform has no tags field
    if len(tags) < len(listing.tags):
        fixes.append(f"{len(listing.tags) - len(tags)} tag(s) removed ({p.name}: max {p.tags_max}, "
                     f"up to {p.tag_len_max} characters, no duplicates)" if p.tags_max else f"{p.name} has no tags: dropped")
    return Listing(title=title, description=listing.description.strip(), tags=tags), fixes


def write(image: bytes, platform: str, lang: str, facts: Facts | None = None, api_key: str | None = None) -> Result:
    facts = facts or Facts()
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return _template(platform, lang, facts, "template")
    try:
        raw = _llm(image, platform, lang, facts, key)
        attrs, listing = Attributes(**raw["attributes"]), Listing(**raw["listing"])
    except Exception as e:  # API down, bad key, refusal, unreadable image: still give the seller a starting point
        log.warning("LLM call failed (%s), using template", type(e).__name__)
        return _template(platform, lang, facts, "template (API error)", f"{type(e).__name__}: {e}"[:300])
    attrs, listing, fixes = guard(attrs, listing, facts)
    listing, rule_fixes = enforce(listing, platform)
    return Result(attrs, listing, platform, lang, "llm", fixes + rule_fixes)


def _template(platform: str, lang: str, facts: Facts, source: str, error: str = "") -> Result:
    """No vision without a key: a fill-in-the-blanks listing from the seller's own facts."""
    de = lang == "de"
    size = f"{'Gr.' if de else 'size'} {facts.size}" if facts.size else ""
    title = " ".join(filter(None, [facts.brand, facts.notes.split(".")[0], size])) or ("Artikel" if de else "Item")
    body = ("Zustand: [bitte ergänzen]. Maße: [bitte ergänzen]. Versand möglich." if de else
            "Condition: [please fill in]. Measurements: [please fill in]. Shipping available.")
    listing, fixes = enforce(Listing(title=title, description=f"{facts.notes}\n\n{body}".strip()), platform)
    return Result(Attributes(brand=facts.brand, size=facts.size), listing, platform, lang, source, fixes, error)


def _llm(image: bytes, platform: str, lang: str, facts: Facts, api_key: str) -> dict:
    import anthropic

    p = PLATFORMS[platform]
    media_type, data = prepare_image(image)
    given = "; ".join(f"{k}: {v}" for k, v in facts.model_dump().items() if v) or "none"
    task = (f"Platform: {p.name} (title max {p.title_max} characters; "
            + (f"up to {p.tags_max} tags of max {p.tag_len_max} characters" if p.tags_max else "no tags") + f"). "
            f"Style: {p.style}. Language: {'German' if lang == 'de' else 'English'}. Seller's facts: {given}.")
    resp = anthropic.Anthropic(api_key=api_key).beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if a safety classifier declines, the API retries on a fallback model
        system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            {"type": "text", "text": task},
        ]}],
    )
    usage["in"] += resp.usage.input_tokens
    usage["out"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        raise RuntimeError("the model declined the request")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))
