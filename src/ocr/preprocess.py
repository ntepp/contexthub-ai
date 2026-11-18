"""Engine-independent clean-up of a page image before OCR (Pillow only, no OpenCV).

* grayscale and EXIF orientation;
* upscaling of low-resolution scans to about ``OCR_DPI`` (Tesseract reads best around
  300 dpi), the resolution being estimated from the pixel size of an A4/Letter page;
* light deskew (±5°) by projection profile: the angle that makes text rows the most
  contrasted wins, and only when it clearly beats the unrotated page;
* blank page detection, so that an empty page is not reported as badly read.
"""

from __future__ import annotations

from PIL import Image, ImageOps, ImageStat

# Long side of an A4 page in inches (Letter is 11): used to estimate a scan's resolution.
_PAGE_LONG_SIDE_INCHES = 11.69
_MAX_UPSCALE = 3.0
_MAX_PIXELS = 40_000_000
_DESKEW_RANGE = 5.0
_DESKEW_STEP = 0.5
_DESKEW_WIDTH = 600
_DESKEW_MIN_GAIN = 1.15
# A page is blank when almost no pixel is dark and the page has very little contrast.
_BLANK_DARK_RATIO = 0.0001  # a single short word already exceeds it
_BLANK_STDDEV = 6.0


def to_grayscale(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    return image.convert("L")


def estimated_dpi(image: Image.Image) -> float:
    return max(image.size) / _PAGE_LONG_SIDE_INCHES


def upscale(image: Image.Image, target_dpi: int) -> tuple[Image.Image, float]:
    """Image scaled towards ``target_dpi`` when it is clearly below; returns the factor applied."""
    dpi = estimated_dpi(image)
    if dpi <= 0 or dpi >= 0.8 * target_dpi:
        return image, 1.0
    factor = min(target_dpi / dpi, _MAX_UPSCALE)
    width, height = image.size
    factor = min(factor, (_MAX_PIXELS / (width * height)) ** 0.5)
    if factor <= 1.05:
        return image, 1.0
    size = (round(width * factor), round(height * factor))
    return image.resize(size, Image.Resampling.LANCZOS), factor


def is_blank(image: Image.Image) -> bool:
    gray = image.convert("L")
    gray.thumbnail((800, 800))
    histogram = gray.histogram()
    dark = sum(histogram[:128]) / max(1, gray.width * gray.height)
    return dark < _BLANK_DARK_RATIO and ImageStat.Stat(gray).stddev[0] < _BLANK_STDDEV


def _profile_score(binary: Image.Image, angle: float) -> float:
    rotated = binary.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0)
    rows = rotated.resize((1, rotated.height), Image.Resampling.BOX)  # mean ink per row
    values = list(rows.getdata())
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def skew_angle(image: Image.Image) -> float:
    """Counter-clockwise angle (degrees) that straightens the text lines; 0 when unsure."""
    small = image.convert("L")
    if small.width > _DESKEW_WIDTH:
        small = small.resize((_DESKEW_WIDTH, max(1, round(small.height * _DESKEW_WIDTH / small.width))))
    # Ink as white on black, so rotation corners (filled with 0) add no ink.
    binary = ImageOps.invert(small).point(lambda v: 255 if v > 96 else 0)
    base = _profile_score(binary, 0.0)
    if base <= 0:
        return 0.0
    best_angle, best_score = 0.0, base
    steps = int(_DESKEW_RANGE / _DESKEW_STEP)
    for i in range(-steps, steps + 1):
        angle = i * _DESKEW_STEP
        if angle == 0:
            continue
        score = _profile_score(binary, angle)
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle if best_score >= base * _DESKEW_MIN_GAIN else 0.0


def deskew(image: Image.Image) -> tuple[Image.Image, float]:
    angle = skew_angle(image)
    if not angle:
        return image, 0.0
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255), angle


def rotate_clockwise(image: Image.Image, degrees: int) -> Image.Image:
    if degrees % 360 == 0:
        return image
    return image.rotate(-degrees, expand=True, fillcolor=255)
