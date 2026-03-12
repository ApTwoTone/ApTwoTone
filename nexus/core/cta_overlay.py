"""
Zoar Bathroom Rentals — CTA Overlay Engine

Takes raw AI-generated images and adds professional Facebook/Instagram ad overlays:
- Headline text with semi-transparent background band
- CTA button (rounded rectangle)
- Optional subline/price badge
- Safe zones: text stays in lower third, never on trailer body/doors

Output: 1080x1920 (9:16) PNG ready for Facebook/Instagram posting.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

# Ensure user site-packages is in path (needed when running via launchd)
_user_site = str(Path.home() / "Library" / "Python" / "3.9" / "lib" / "python" / "site-packages")
if _user_site not in sys.path:
    sys.path.insert(0, _user_site)

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Font paths (macOS system fonts) ───────────────────────────────────
FONT_BOLD = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_REGULAR = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_CONDENSED = "/System/Library/Fonts/Avenir Next Condensed.ttc"

# Fallbacks
if not os.path.exists(FONT_BOLD):
    FONT_BOLD = "/System/Library/Fonts/Helvetica.ttc"
if not os.path.exists(FONT_REGULAR):
    FONT_REGULAR = "/System/Library/Fonts/Helvetica.ttc"

# ── FB/IG ad dimensions ──────────────────────────────────────────────
AD_WIDTH = 1080
AD_HEIGHT = 1920  # 9:16

OUTPUT_DIR = Path.home() / ".nexus" / "higgsfield" / "static_ads"


def _load_font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    """Load a TrueType font with fallback to default."""
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()


def _rounded_rectangle(draw: ImageDraw.ImageDraw, xy, radius: int, fill):
    """Draw a rounded rectangle (Pillow < 10.0 compat)."""
    x0, y0, x1, y1 = xy
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    # Use built-in rounded_rectangle if available (Pillow >= 8.2)
    try:
        draw.rounded_rectangle(xy, radius=r, fill=fill)
        return
    except AttributeError:
        pass
    # Manual fallback
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.pieslice([x0, y0, x0 + 2*r, y0 + 2*r], 180, 270, fill=fill)
    draw.pieslice([x1 - 2*r, y0, x1, y0 + 2*r], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2*r, x0 + 2*r, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2*r, y1 - 2*r, x1, y1], 0, 90, fill=fill)


def _text_size(draw, text, font):
    """Get text bounding box size (compatible with Pillow 9+)."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        w, _ = _text_size(draw, test, font)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def apply_cta_overlay(
    image_path: str,
    headline: str,
    cta_text: str,
    subline: str = "",
    palette: dict = None,
    output_path: str = None,
    campaign_id: str = "",
) -> str:
    """
    Apply CTA overlay to a raw image and save as Facebook-ready PNG.

    Args:
        image_path: Path to source image
        headline: Main headline text (e.g. "Your guests remember comfort.")
        cta_text: Button text (e.g. "Check Date Availability")
        subline: Optional subline (e.g. "4-stall | Starting at $1,000")
        palette: Color palette dict with headline_bg, headline_text, cta_bg, cta_text
        output_path: Where to save (auto-generates if not provided)
        campaign_id: For naming the output file

    Returns:
        Path to the generated ad image
    """
    from core.ad_prompts import PALETTES

    if palette is None:
        palette = PALETTES.get("deep_navy", PALETTES["deep_navy"])
    elif isinstance(palette, str):
        palette = PALETTES.get(palette, PALETTES["deep_navy"])

    # Load and resize image to 1080x1920
    img = Image.open(image_path).convert("RGBA")

    # Resize to fill 1080x1920 (crop-to-fill)
    target_ratio = AD_WIDTH / AD_HEIGHT
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # Image is wider — scale by height, crop width
        new_h = AD_HEIGHT
        new_w = int(img.width * (AD_HEIGHT / img.height))
    else:
        # Image is taller — scale by width, crop height
        new_w = AD_WIDTH
        new_h = int(img.height * (AD_WIDTH / img.width))

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - AD_WIDTH) // 2
    top = (new_h - AD_HEIGHT) // 2
    img = img.crop((left, top, left + AD_WIDTH, top + AD_HEIGHT))

    # Create overlay layer
    overlay = Image.new("RGBA", (AD_WIDTH, AD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ── Layout: Lower third zone (below 65% of image) ────────────
    # This keeps text OFF the trailer body per the copy rules
    zone_top = int(AD_HEIGHT * 0.72)  # Start text zone at 72% down
    zone_bottom = AD_HEIGHT - 60       # 60px margin from bottom
    zone_center_x = AD_WIDTH // 2

    # ── Gradient background band in lower portion ─────────────────
    # Semi-transparent dark gradient for text readability
    gradient = Image.new("RGBA", (AD_WIDTH, AD_HEIGHT - zone_top + 80), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(gradient.height):
        # Ease from 0 opacity to target opacity
        progress = y / gradient.height
        alpha = int(progress * progress * 160)  # Quadratic ease-in
        grad_draw.line([(0, y), (AD_WIDTH, y)], fill=(0, 0, 0, alpha))
    overlay.paste(gradient, (0, zone_top - 80), gradient)

    # ── Headline ──────────────────────────────────────────────────
    headline_font_size = 52
    if len(headline) > 30:
        headline_font_size = 44
    if len(headline) > 45:
        headline_font_size = 38

    headline_font = _load_font(FONT_BOLD, headline_font_size, index=1)  # index=1 = Bold
    headline_color = palette.get("headline_text", (255, 255, 255))

    # Wrap headline text
    margin_x = 72
    max_text_width = AD_WIDTH - margin_x * 2
    headline_lines = _wrap_text(headline, headline_font, max_text_width, draw)

    # Calculate headline block height
    line_heights = []
    for line in headline_lines:
        _, lh = _text_size(draw, line, headline_font)
        line_heights.append(lh)
    line_spacing = 8
    total_headline_h = sum(line_heights) + line_spacing * (len(headline_lines) - 1)

    # Position headline
    headline_y = zone_top + 20

    # Draw headline with text shadow for readability
    y_cursor = headline_y
    for i, line in enumerate(headline_lines):
        lw, lh = _text_size(draw, line, headline_font)
        # Center text
        lx = (AD_WIDTH - lw) // 2
        # Text shadow
        draw.text((lx + 2, y_cursor + 2), line, font=headline_font, fill=(0, 0, 0, 140))
        # Main text
        draw.text((lx, y_cursor), line, font=headline_font, fill=headline_color)
        y_cursor += lh + line_spacing

    # ── Subline (if provided) ─────────────────────────────────────
    if subline:
        subline_font = _load_font(FONT_REGULAR, 28, index=0)  # Regular weight
        subline_color = palette.get("subline_text", (255, 255, 255, 200))
        sw, sh = _text_size(draw, subline, subline_font)
        subline_y = y_cursor + 12
        sx = (AD_WIDTH - sw) // 2
        draw.text((sx + 1, subline_y + 1), subline, font=subline_font, fill=(0, 0, 0, 100))
        draw.text((sx, subline_y), subline, font=subline_font, fill=subline_color)
        y_cursor = subline_y + sh

    # ── CTA Button ────────────────────────────────────────────────
    cta_font = _load_font(FONT_BOLD, 30, index=1)
    cta_color = palette.get("cta_text", (35, 35, 35))
    cta_bg = palette.get("cta_bg", (212, 175, 85, 240))

    cta_w, cta_h = _text_size(draw, cta_text, cta_font)
    btn_padding_x = 48
    btn_padding_y = 20
    btn_w = cta_w + btn_padding_x * 2
    btn_h = cta_h + btn_padding_y * 2

    btn_x = (AD_WIDTH - btn_w) // 2
    btn_y = y_cursor + 32

    # Ensure button doesn't go below safe zone
    if btn_y + btn_h > zone_bottom:
        btn_y = zone_bottom - btn_h - 10

    # Button shadow
    shadow_rect = (btn_x + 3, btn_y + 3, btn_x + btn_w + 3, btn_y + btn_h + 3)
    _rounded_rectangle(draw, shadow_rect, 16, fill=(0, 0, 0, 60))

    # Button background
    btn_rect = (btn_x, btn_y, btn_x + btn_w, btn_y + btn_h)
    _rounded_rectangle(draw, btn_rect, 16, fill=cta_bg)

    # Button text
    cta_x = btn_x + btn_padding_x
    cta_y = btn_y + btn_padding_y
    draw.text((cta_x, cta_y), cta_text, font=cta_font, fill=cta_color)

    # ── Composite ─────────────────────────────────────────────────
    result = Image.alpha_composite(img, overlay)
    result = result.convert("RGB")  # Convert to RGB for JPEG/PNG save

    # ── Save ──────────────────────────────────────────────────────
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        name = campaign_id or "ad"
        # Find next available index
        existing = list(OUTPUT_DIR.glob(f"{name}_*.png"))
        idx = len(existing) + 1
        output_path = str(OUTPUT_DIR / f"{name}_{idx:03d}.png")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, "PNG", quality=95)
    print(f"[CTA] Saved ad image: {output_path}")
    return output_path


def batch_overlay(
    image_paths: list[str],
    campaign: dict,
) -> list[str]:
    """Apply CTA overlay to multiple images using the same campaign config.
    Returns list of output paths."""
    results = []
    for i, img_path in enumerate(image_paths):
        try:
            out = apply_cta_overlay(
                image_path=img_path,
                headline=campaign["headline"],
                cta_text=campaign["cta"],
                subline=campaign.get("subline", ""),
                palette=campaign.get("palette", "deep_navy"),
                campaign_id=campaign.get("id", "ad"),
            )
            results.append(out)
        except Exception as e:
            print(f"[CTA] Error on image {i+1}: {e}")
    return results


def preview_all_palettes(image_path: str) -> list[str]:
    """Generate preview images with all palettes for A/B testing."""
    from core.ad_prompts import PALETTES
    results = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, pal in PALETTES.items():
        out = apply_cta_overlay(
            image_path=image_path,
            headline="Your guests remember comfort.",
            cta_text="Get Event Pricing",
            subline="4-stall | Starting at $1,000",
            palette=pal,
            output_path=str(OUTPUT_DIR / f"palette_preview_{name}.png"),
        )
        results.append(out)
    return results
