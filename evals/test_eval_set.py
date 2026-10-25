"""Sanity checks on the eval set and the scoring, no API calls."""
import json
import sys
from pathlib import Path

from PIL import Image

from listing_writer.listing import Attributes, Listing, Result

HERE = Path(__file__).parent
GOLD = json.loads((HERE / "gold.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(HERE))
from run_eval import lang_of, score  # noqa: E402


def test_every_labeled_image_exists_is_small_and_credited():
    credits = (HERE / "images" / "CREDITS.md").read_text()
    for name in GOLD:
        img = Image.open(HERE / "images" / name)
        assert max(img.size) <= 1200 and not img.getexif()
        assert name in credits or GOLD[name]["source"] != "public domain"


def test_set_has_visible_and_hidden_brands():
    brands = [g["brand"] for g in GOLD.values()]
    assert sum(bool(b) for b in brands) >= 3 and sum(not b for b in brands) >= 3


def result(brand="", fixes=(), lang="en", title="Leather handbag", desc="A brown leather bag in good condition."):
    return Result(Attributes(item_type="handbag", brand=brand, color="brown"), Listing(title=title, description=desc),
                  "ebay", lang, "llm", list(fixes))


def test_score_counts_invented_brand_before_and_after_guardrail():
    gold = GOLD["leather-handbag.jpg"]
    c, errors = score(result(fixes=["brand 'Prada' is not readable in the photo and was not given: removed"]), gold)
    assert (c["invented_before"], c["invented_after"], c["brand_ok"]) == (1, 0, 1)
    assert errors == ["brand invented: 'Prada' (removed by the guardrail)"]
    c, errors = score(result(brand="Prada"), gold)
    assert (c["invented_before"], c["invented_after"], c["brand_ok"]) == (1, 1, 0)


def test_score_visible_brand_and_publisher():
    c, _ = score(result(brand="AVIA"), GOLD["running-shoe.jpg"] | {"item_words": ["handbag"]})
    assert c["brand_ok"] and not c["invented_before"]
    c, errors = score(result(brand="Houghton Mifflin", title="Bit by Bit book"), GOLD["book-back-cover.jpg"] | {"item_words": ["book"]})
    assert c["brand_ok"] and not c["invented_before"] and c["facts"] == 1


def test_lang_of():
    assert lang_of("Die Tasche ist in sehr gutem Zustand und wurde kaum getragen.") == "de"
    assert lang_of("The bag is in very good condition.") == "en"
