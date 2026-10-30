import json
from pathlib import Path
import re

from textual.color import Color
from textual.theme import Theme


MAX_PALETTE_BYTES = 64 * 1024


def load_wal_theme(path: Path) -> Theme | None:
    """ Read a pywal palette, returning None if it's unavailable """

    try:
        with path.open("rb") as palette_file:
            raw = palette_file.read(MAX_PALETTE_BYTES + 1)

        if len(raw) > MAX_PALETTE_BYTES:
            return None

        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    special = data.get("special")
    colors = data.get("colors")

    if not isinstance(special, dict) or not isinstance(colors, dict):
        return None

    selected = {
        "background": special.get("background"),
        "foreground": special.get("foreground"),
        "primary": colors.get("color4"),
        "secondary": colors.get("color5"),
        "accent": colors.get("color6"),
        "error": colors.get("color1"),
        "success": colors.get("color2"),
        "warning": colors.get("color3")
    }

    for value in selected.values():
        if not isinstance(value, str):
            return None

        if re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            return None

    background = Color.parse(selected["background"])
    foreground = Color.parse(selected["foreground"])

    # Derive panel colors from wallpaper palette
    surface = background.blend(foreground, 0.06)
    panel = background.blend(foreground, 0.12)

    return Theme (
        name="oceanus-wal",
        primary=selected["primary"],
        secondary=selected["secondary"],
        accent=selected["accent"],
        error=selected["error"],
        success=selected["success"],
        warning=selected["warning"],
        foreground=selected["foreground"],
        background=selected["background"],
        surface=surface.hex,
        panel=panel.hex,
        dark=background.brightness < foreground.brightness
    )