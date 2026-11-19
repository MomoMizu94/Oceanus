"""Render the opening portrait using the terminal's own background."""

from functools import lru_cache
from importlib.resources import files

from PIL import Image, ImageChops, ImageFilter
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.widget import Widget


# Two pixel rows fit in one terminal row
PORTRAIT_SIZES = (12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96)


@lru_cache(maxsize=1)
def portrait_image() -> Image.Image:
    asset = files("agent_core").joinpath("assets", "greeting.png")
    with asset.open("rb") as source, Image.open(source) as image:
        return image.convert("RGBA")


@lru_cache(maxsize=2 * len(PORTRAIT_SIZES))
def portrait_frame(size: int, lowered: bool, backdrop: tuple[int, int, int]) -> Text:
    side = size - 2
    portrait = portrait_image().resize((side, side), Image.Resampling.LANCZOS)
    # Restore local contrast in fine lines after shrinking the sprite
    colors = portrait.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=0.8, percent=130, threshold=3)
    )

    # Keep the face visible and taper the shoulders into the transcript
    row_opacity = []
    for y in range(side):
        height = y / (side - 1)
        shoulder_fade = max(0.0, (height - 0.65) / 0.35)
        row_opacity.append(round(92 - 85 * shoulder_fade))
    fade = Image.new("L", (1, side))
    fade.putdata(row_opacity)
    coverage = ImageChops.multiply(portrait.getchannel("A"), fade.resize(portrait.size))
    background = Image.new("RGB", portrait.size, backdrop)
    portrait = Image.composite(colors, background, coverage)
    portrait.putalpha(coverage)
    canvas = Image.new("RGBA", (size, size))
    canvas.paste(portrait, (1, 1 + int(lowered)))
    pixels = canvas.load()
    art = Text(no_wrap=True)
    for y in range(0, size, 2):
        if y:
            art.append("\n")
        for x in range(size):
            top = pixels[x, y]
            bottom = pixels[x, y + 1]
            # A half-empty cell lets the current terminal theme show through
            if top[3]:
                lower_color = Color.from_rgb(*bottom[:3]) if bottom[3] else None
                art.append("▀", Style(color=Color.from_rgb(*top[:3]), bgcolor=lower_color))
            elif bottom[3]:
                art.append("▄", Style(color=Color.from_rgb(*bottom[:3])))
            else:
                art.append(" ")
    return art


class GreetingPortrait(Widget):
    DEFAULT_CSS = """
    GreetingPortrait { width: 1fr; height: 1fr; content-align: center middle; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.lowered = False

    def on_mount(self) -> None:
        if self.app.animation_level == "full":
            self.set_interval(0.85, self.advance)

    def pixel_size(self) -> int:
        available = min(self.size.width, 2 * self.size.height)
        for size in reversed(PORTRAIT_SIZES):
            if size <= available:
                # Round to whole terminal rows so both half blocks fit
                rows = round((size // 2) * 0.8)
                return rows * 2
        return 0

    def advance(self) -> None:
        if self.app.screen is self.screen and self.pixel_size():
            self.lowered = not self.lowered
            self.refresh()

    def render(self) -> Text:
        size = self.pixel_size()
        if not size:
            return Text()
        backdrop = self.background_colors[1].rgb
        return portrait_frame(size, self.lowered, backdrop)
