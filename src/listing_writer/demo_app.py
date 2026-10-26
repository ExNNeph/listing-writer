"""Local web demo, started with `listing-writer demo`."""
import os
from pathlib import Path

import streamlit as st

from listing_writer.listing import MODEL, Facts, write
from listing_writer.platforms import PLATFORMS

SAMPLES = Path(__file__).resolve().parents[2] / "evals" / "images"  # present when run from a clone of the repo

st.set_page_config(page_title="Listing Writer demo", page_icon="🏷️", layout="wide")
st.title("🏷️ Listing Writer")
st.caption("Photo in, listing out: title, description and tags for Vinted, eBay or Etsy, in German or English. "
           "Brands and sizes that the photo does not show are removed, platform limits are enforced in code.")

with st.sidebar:
    platform = st.radio("Platform", list(PLATFORMS), format_func=lambda p: PLATFORMS[p].name, horizontal=True)
    lang = st.radio("Language", ["de", "en"], format_func={"de": "Deutsch", "en": "English"}.get, horizontal=True)
    st.markdown("**What you know** (treated as true)")
    facts = Facts(brand=st.text_input("Brand", placeholder="only if you know it"),
                  size=st.text_input("Size", placeholder="e.g. M, 38, W32/L34"),
                  notes=st.text_area("Notes", placeholder="e.g. worn twice, small stain on the sleeve", height=80))
    api_key = st.text_input("Anthropic API key (optional)", type="password",
                            help="Used only for your requests in this local session, never stored.")
    if api_key or os.getenv("ANTHROPIC_API_KEY"):
        where = "sidebar" if api_key else "ANTHROPIC_API_KEY environment variable"
        st.success(f"🔑 Key set ({where}): Claude ({MODEL}) looks at the photo.")
    else:
        st.info("No key: a fill-in template from your own facts only. Paste a key and press Enter to let Claude "
                "look at the photo.")

choices = ["Upload a photo", "Take a photo"] + (["Sample photo"] if SAMPLES.is_dir() else [])
source = st.radio("Photo", choices, horizontal=True)
image = None
if source == "Upload a photo":
    upload = st.file_uploader("JPG, PNG or WebP", type=["jpg", "jpeg", "png", "webp"])
    image = upload.getvalue() if upload else None
elif source == "Take a photo":
    shot = st.camera_input("Take a photo of the item")
    image = shot.getvalue() if shot else None
else:
    names = sorted(p.name for p in SAMPLES.glob("*.jpg"))
    name = st.selectbox("Sample photo (public domain or generated, see evals/images/CREDITS.md)", names)
    image = (SAMPLES / name).read_bytes()
if image:
    st.image(image, width=360)


@st.cache_data(show_spinner="Looking at the photo and writing the listing…")
def cached_write(photo: bytes, platform: str, lang: str, facts_json: str, key: str):
    # cached so reruns (clicking, scrolling) never call or pay for the API twice
    return write(photo, platform, lang, Facts.model_validate_json(facts_json), key or None)


if st.button("Write listing", type="primary", disabled=image is None):
    st.session_state.result = cached_write(image, platform, lang, facts.model_dump_json(), api_key)
    st.session_state.result_for = (image, platform, lang, facts, api_key)

r = st.session_state.get("result")
if r is None:
    st.info("👆 Choose a photo, then click **Write listing**.")
    st.stop()
if st.session_state.get("result_for") != (image, platform, lang, facts, api_key):
    st.warning("Photo, platform, language, facts or key changed since this result. Click **Write listing** again.")

p = PLATFORMS[r.platform]
if r.source == "llm":
    st.success(f"Written by Claude ({MODEL}) from the photo for {p.name}.")
elif r.source == "template (API error)":
    st.error(f"The Claude call failed, so a fill-in template is shown.\n\n`{r.error}`")
else:
    st.info("Fill-in template from your own facts (no key, so nothing looked at the photo).")

st.subheader(f"{p.name} listing")
st.caption(f"Title: {len(r.listing.title)} / {p.title_max} characters · copy with the button on the right of each box")
st.code(r.listing.title, language=None, wrap_lines=True)
st.code(r.listing.description, language=None, wrap_lines=True)
if r.listing.tags:
    st.code((" ".join(f"#{t.replace(' ', '')}" for t in r.listing.tags) if r.platform == "vinted"
             else ", ".join(r.listing.tags)), language=None, wrap_lines=True)

for fix in r.fixes:
    (st.warning if fix.startswith(("brand", "size")) else st.info)(f"Corrected: {fix}")

if r.source == "llm":
    a = r.attributes
    with st.expander("What Claude saw in the photo", expanded=True):
        st.table({"": ["item", "brand", "size", "color", "material", "condition", "readable text", "defects"],
                  "seen": [a.item_type or a.category, a.brand or "not visible", a.size or "not visible", a.color,
                           a.material or "—", a.condition, ", ".join(a.visible_text) or "—", ", ".join(a.defects) or "—"]})
