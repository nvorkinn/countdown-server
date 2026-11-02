"""Rebuild the config page's layout and weather pictures from the renderer's snapshots, at twice the size they're shown.

    uv run --with pillow scripts/build_picker_pictures.py ../countdown

The argument is a checkout of nvorkinn/countdown (the renderer); its tests/images/ holds a full-size snapshot of every
layout and weather design. The pictures land in broker/static/layouts/ and broker/static/weather/:

- an HD screen's layouts (1404x1872 hung portrait, 1872x1404 landscape) at half size, 702x936 and 936x702, as the page
  shows them up to 351 and 468 CSS pixels wide;
- a 7.5" screen's portrait layouts (480x800) at full size, shown up to 240 wide;
- the weather designs as the top 690 rows of an HD portrait screen, at two thirds, 936x460, shown up to 468 wide.

So each is sharp on a 2x (retina) screen, and the browser shrinks it smoothly on a 1x one. The broker has no Pillow,
hence `--with pillow`.
"""

import sys
from pathlib import Path

from PIL import Image  # ty: ignore[unresolved-import] -- run with `uv run --with pillow`; the broker has no Pillow

STATIC = Path(__file__).resolve().parent.parent / "broker" / "static"

# Picture -> snapshot (tests/images/<name>.png). Classic and sidebar are each orientation's default layout, which the
# style snapshots draw (in Hero); "pages" shows its going-out page.
HD_PORTRAIT = {
    "classic": "screen_hd_portrait_style_hero",
    "masthead": "screen_hd_portrait_layout_masthead",
    "column": "screen_hd_portrait_layout_column",
    "lockscreen": "screen_hd_portrait_layout_lockscreen",
    "timeline": "screen_hd_portrait_layout_timeline",
    "board": "screen_hd_portrait_layout_board",
    "pages": "screen_hd_portrait_layout_pages_out",
    "track": "screen_hd_portrait_layout_track",
    "ledger": "screen_hd_portrait_layout_ledger",
    "queue": "screen_hd_portrait_layout_queue",
}
HD_LANDSCAPE = {
    "landscape-sidebar": "screen_hd_landscape_style_hero",
    "landscape-board": "screen_hd_landscape_layout_board",
    "landscape-column": "screen_hd_landscape_layout_column",
    "landscape-focus": "screen_hd_landscape_layout_focus",
    "landscape-newspaper": "screen_hd_landscape_layout_newspaper",
    "landscape-pages": "screen_hd_landscape_layout_pages_out",
}
SMALL_PORTRAIT = {
    "small-classic": "screen_portrait_style_hero",
    "small-track": "screen_portrait_layout_track",
    "small-ledger": "screen_portrait_layout_ledger",
    "small-queue": "screen_portrait_layout_queue",
}
# Columns is the default weather design, which the classic snapshot draws.
WEATHER = {
    "columns": "screen_hd_portrait_style_hero",
    "overview": "screen_hd_portrait_weather_overview",
    "poster": "screen_hd_portrait_weather_poster",
    "timeline": "screen_hd_portrait_weather_timeline",
}
WEATHER_ROWS = 690


def main() -> int:
    """Write every picture; return the exit status."""
    if len(sys.argv) != 2:  # noqa: PLR2004 -- the one argument
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    images = Path(sys.argv[1]) / "tests" / "images"
    for name, snapshot in HD_PORTRAIT.items():
        _save(_open(images, snapshot), (702, 936), STATIC / "layouts" / f"{name}.png")
    for name, snapshot in HD_LANDSCAPE.items():
        _save(_open(images, snapshot), (936, 702), STATIC / "layouts" / f"{name}.png")
    for name, snapshot in SMALL_PORTRAIT.items():
        _save(_open(images, snapshot), (480, 800), STATIC / "layouts" / f"{name}.png")
    for name, snapshot in WEATHER.items():
        screen = _open(images, snapshot)
        _save(screen.crop((0, 0, screen.width, WEATHER_ROWS)), (936, 460), STATIC / "weather" / f"{name}.png")
    return 0


def _open(images: Path, snapshot: str) -> Image.Image:
    return Image.open(images / f"{snapshot}.png").convert("L")


def _save(image: Image.Image, size: tuple[int, int], path: Path) -> None:
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    image.save(path, optimize=True)
    print(f"{path.relative_to(STATIC.parent.parent)}: {size[0]}x{size[1]}")


if __name__ == "__main__":
    sys.exit(main())
