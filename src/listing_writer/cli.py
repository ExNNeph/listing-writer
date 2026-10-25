import argparse
import subprocess
import sys
from pathlib import Path

from .listing import Facts, write
from .platforms import PLATFORMS

DEMO_APP = Path(__file__).with_name("demo_app.py")


def demo() -> None:
    """Start the local web demo on http://localhost:8501 and open the browser."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit('The demo needs Streamlit: pip install -e ".[demo]"')
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(DEMO_APP), "--server.address", "localhost",
                    "--server.port", "8501", "--browser.gatherUsageStats", "false",
                    "--client.toolbarMode", "minimal",  # hides Streamlit's "Deploy" button
                    "--server.fileWatcherType", "none"])  # no code reloading needed, and no "install Watchdog" hint


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["demo"]:
        return demo()
    p = argparse.ArgumentParser(prog="listing-writer", description="Write a marketplace listing from a product "
                                "photo. Run `listing-writer demo` for the web demo.")
    p.add_argument("photo", type=Path, help="product photo (jpg, png, webp, heic not supported)")
    p.add_argument("--platform", choices=PLATFORMS, default="vinted")
    p.add_argument("--lang", choices=["de", "en"], default="de")
    p.add_argument("--brand", default="", help="brand, if you know it (treated as true)")
    p.add_argument("--size", default="", help="size, if you know it (treated as true)")
    p.add_argument("--notes", default="", help="anything else the photo cannot show, e.g. 'worn twice'")
    args = p.parse_args(argv)

    r = write(args.photo.read_bytes(), args.platform, args.lang, Facts(brand=args.brand, size=args.size, notes=args.notes))
    print(f"{PLATFORMS[r.platform].name} listing ({r.lang}, {r.source})" + (f": {r.error}" if r.error else ""))
    print(f"\nTitle: {r.listing.title}\n\n{r.listing.description}")
    if r.listing.tags:
        print("\nTags: " + ", ".join(r.listing.tags))
    a = r.attributes
    print(f"\nSeen: {a.item_type or '?'} | brand: {a.brand or 'not visible'} | size: {a.size or 'not visible'} | "
          f"color: {a.color or '?'} | condition: {a.condition or '?'}")
    for fix in r.fixes:
        print(f"  fixed: {fix}")
