import base64
import io

import pytest
from PIL import Image

from listing_writer import cli
from listing_writer import listing as lw
from listing_writer.listing import Attributes, Facts, Listing


def png(width=3000, height=2000, exif_rotation=None) -> bytes:
    img, buf = Image.new("RGB", (width, height), "navy"), io.BytesIO()
    if exif_rotation:
        exif = Image.Exif()
        exif[0x0112] = exif_rotation  # orientation tag, as phones write it
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def test_prepare_image_shrinks_rotates_and_strips_metadata():
    media, data = lw.prepare_image(png(4000, 3000, exif_rotation=6))  # 6 = rotated 90 degrees
    img = Image.open(io.BytesIO(base64.b64decode(data)))
    assert media == "image/jpeg" and max(img.size) == lw.MAX_SIDE
    assert img.size[1] > img.size[0]  # landscape file + rotation tag -> portrait photo
    assert not img.getexif()  # GPS and camera data are not sent anywhere


ATTRS = Attributes(item_type="jeans", brand="Levi's", size="W32", color="blue", visible_text=["Levi's", "501"])
LISTING = Listing(title="Levi's 501 jeans W32 blue", description="Classic Levi's jeans in W32.",
                  tags=["levis", "Levi's jeans", "denim"])


def test_guard_keeps_brand_seen_on_label_and_removes_unseen_size():
    attrs, listing, fixes = lw.guard(ATTRS, LISTING, Facts())
    assert attrs.brand == "Levi's" and attrs.size == ""
    assert listing.title == "Levi's 501 jeans blue" and "W32" not in listing.description
    assert fixes == ["size 'W32' is not readable in the photo and was not given: removed"]


def test_guard_removes_invented_brand_everywhere():
    attrs, listing, fixes = lw.guard(ATTRS.model_copy(update={"visible_text": ["501"]}), LISTING, Facts(size="W32"))
    assert attrs.brand == "" and attrs.size == "W32"  # size is the seller's fact
    assert "Levi's" not in listing.title + listing.description and listing.tags == ["denim"]  # "levis" too
    assert len(fixes) == 1 and fixes[0].startswith("brand 'Levi's'")


def test_guard_accepts_brand_given_by_seller():
    _, _, fixes = lw.guard(ATTRS.model_copy(update={"visible_text": []}), LISTING, Facts(brand="levi's", size="W32"))
    assert fixes == []


@pytest.mark.parametrize("platform,title_max,tags_max", [("ebay", 80, 0), ("etsy", 140, 13), ("vinted", 60, 5)])
def test_enforce_platform_rules(platform, title_max, tags_max):
    long = Listing(title="Vintage " + "wool " * 40 + "coat", description=" text ",
                   tags=[f"#tag{i}" for i in range(20)] + ["#tag1", "a tag that is far too long"])
    out, fixes = lw.enforce(long, platform)
    assert len(out.title) <= title_max and not out.title.endswith(" ")
    assert len(out.tags) == tags_max and all(len(t) <= 20 and not t.startswith("#") for t in out.tags)
    assert out.description == "text" and fixes


def test_enforce_leaves_good_listing_alone():
    out, fixes = lw.enforce(Listing(title="Blue denim jacket", description="d", tags=["#denim", "#jacket"]), "vinted")
    assert fixes == [] and out.tags == ["denim", "jacket"]


def fake(monkeypatch, raw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(lw, "_llm", lambda *a: raw)


def test_write_runs_guard_and_rules(monkeypatch):
    fake(monkeypatch, {"attributes": {**ATTRS.model_dump(), "visible_text": []},
                       "listing": {**LISTING.model_dump(), "tags": ["levis", "jeans"]}})
    r = lw.write(png(), "ebay", "en")
    assert r.source == "llm" and r.attributes.brand == "" and r.listing.tags == []
    assert [f.split(" ")[0] for f in r.fixes] == ["brand", "size", "eBay"]


def test_api_error_falls_back_to_template(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    def boom(*a):
        raise RuntimeError("overloaded")

    monkeypatch.setattr(lw, "_llm", boom)
    r = lw.write(png(), "vinted", "de", Facts(brand="Zara", size="M", notes="Leinenhemd. Zweimal getragen."))
    assert (r.source, r.error) == ("template (API error)", "RuntimeError: overloaded")
    assert r.listing.title == "Zara Leinenhemd Gr. M" and "[bitte ergänzen]" in r.listing.description


def test_cli_without_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    photo = tmp_path / "p.png"
    photo.write_bytes(png(10, 10))
    cli.main([str(photo), "--platform", "etsy", "--lang", "en", "--brand", "Acme", "--notes", "Ceramic mug"])
    out = capsys.readouterr().out
    assert "Etsy listing (en, template)" in out and "Title: Acme Ceramic mug" in out
