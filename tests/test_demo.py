import builtins

import pytest
from streamlit.testing.v1 import AppTest

from listing_writer import cli
from listing_writer import listing as lw

RAW = {"attributes": {"category": "shoes", "item_type": "running shoe", "brand": "Avia", "color": "navy blue",
                      "material": "mesh", "size": "44", "condition": "like new", "visible_text": ["AVIA"], "defects": []},
       "listing": {"title": "Avia Laufschuh Gr. 44 navy blau wie neu", "description": "Avia Laufschuh, Größe 44.",
                   "tags": ["#avia", "#laufschuhe", "#avia", "#sneaker"]}}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return AppTest.from_file(str(cli.DEMO_APP), default_timeout=20)  # the file `listing-writer demo` starts


def sample(at):
    next(r for r in at.radio if r.label == "Photo").set_value("Sample photo").run()
    return at


def test_demo_waits_for_a_photo(app):
    at = app.run()
    assert not at.exception and at.button[0].disabled


def test_template_without_key(app):
    at = sample(app.run())
    at.text_input[0].input("Avia").run()
    at.button[0].click().run()
    assert not at.exception and at.session_state["result"].source == "template"
    assert "Avia" in at.code[0].value


def test_claude_result_shows_listing_fixes_and_attributes(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(lw, "_llm", lambda *a: RAW)
    at = sample(app.run())
    at.button[0].click().run()
    assert not at.exception
    r = at.session_state["result"]
    assert r.source == "llm" and r.attributes.size == ""  # size 44 was not readable and not given
    assert "44" not in at.code[0].value
    assert at.code[2].value == "#avia #laufschuhe #sneaker"  # duplicate removed, Vinted hashtags
    assert any("size '44'" in w.value for w in at.warning)
    assert at.table  # what Claude saw


def test_demo_without_streamlit_gives_install_hint(monkeypatch):
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "streamlit" else real(n, *a, **k))
    with pytest.raises(SystemExit, match="pip install"):
        cli.demo()
