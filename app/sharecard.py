"""Shareable verdict card. Same findings as the text, no duplicate proof blocks."""

from html import escape

from .checks import TOP_STEPS
from .config import BOT_HANDLE

_BAND = {"Low": "#067647", "Elevated": "#B54708", "High": "#B42318"}

W = 1080
PAD = 64
BODY = W - 2 * PAD


def _rows(verdict: dict) -> list[dict]:
    return verdict.get("findings") or []


def svg(verdict: dict) -> str:
    """Minimal fallback when Pillow is unavailable."""
    color = _BAND.get(verdict["band"], "#444")
    parts, y = [], 230
    for i, item in enumerate(_rows(verdict), 1):
        parts.append(
            f"<text x='{PAD}' y='{y}' font-size='30' font-weight='600' fill='#111827'>"
            f"{i}. {escape(item['headline'])}</text>"
            f"<text x='{PAD}' y='{y + 38}' font-size='24' fill='#4b5563'>"
            f"“{escape(item['quote'])}”</text>"
            f"<text x='{PAD}' y='{y + 74}' font-size='23' fill='#111827'>"
            f"→ {escape(item['action'])}</text>"
        )
        y += 122
    y += 30
    parts.append(f"<text x='{PAD}' y='{y}' font-size='22' fill='#6b7280'>DO NOW</text>")
    for i, step in enumerate(TOP_STEPS.get(verdict["band"], TOP_STEPS["Low"]), 1):
        y += 32
        parts.append(f"<text x='{PAD}' y='{y}' font-size='22' fill='#374151'>{i}. {escape(step)}</text>")
    height = y + 90
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{height}'>"
        f"<rect width='{W}' height='{height}' fill='#ffffff'/>"
        f"<rect width='{W}' height='10' fill='{color}'/>"
        f"<text x='{PAD}' y='78' font-size='20' letter-spacing='3' fill='#6b7280' font-family='sans-serif'>SUSPICION</text>"
        f"<text x='{PAD}' y='150' font-size='60' font-weight='700' fill='{color}' font-family='sans-serif'>"
        f"{escape(verdict['band'])} risk</text>"
        f"<line x1='{PAD}' y1='190' x2='{W - PAD}' y2='190' stroke='#e5e7eb'/>"
        f"<g font-family='sans-serif'>{''.join(parts)}</g>"
        f"</svg>"
    )


def png(verdict: dict) -> bytes | None:
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    def font(size: int, bold: bool = False):
        for path in (
            r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    f_brand = font(20, bold=True)
    f_band = font(62, bold=True)
    f_meta = font(22)
    f_head = font(31, bold=True)
    f_quote = font(24)
    f_act = font(24)
    f_label = font(21, bold=True)
    f_check = font(22)

    color = _BAND.get(verdict["band"], "#444444")
    band = verdict["band"]
    items = _rows(verdict)
    found = verdict.get("checks") or []
    steps = TOP_STEPS.get(band, TOP_STEPS["Low"])

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def lines(text: str, fnt, width: int = BODY, limit: int | None = None) -> list[str]:
        out, current = [], ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if probe.textbbox((0, 0), trial, font=fnt)[2] <= width:
                current = trial
            else:
                if current:
                    out.append(current)
                current = word
        if current:
            out.append(current)
        if limit and len(out) > limit:
            out = out[:limit]
            out[-1] = out[-1].rstrip(" ,.;:") + "…"
        return out

    wrapped = []
    for i, item in enumerate(items, 1):
        wrapped.append({
            "head": lines(f"{i}. {item['headline']}", f_head, BODY, 2),
            "quote": lines(f"“{item['quote']}”", f_quote, BODY, 2) if item.get("quote") else [],
            "act": lines("→ " + item["action"], f_act, BODY, 2),
        })
    check_rows = [lines(f"{c['label']}: {c['value']}", f_check, BODY, 1)[0] for c in found]
    step_rows = [lines(f"{i}. {step}", f_check, BODY, 1)[0] for i, step in enumerate(steps, 1)]

    top, gap, label_gap = 210, 34, 44
    for block in wrapped:
        top += len(block["head"]) * 42 + len(block["quote"]) * 34 + len(block["act"]) * 34 + gap
    top += label_gap + len(check_rows) * 34 + 16 if check_rows else 0
    top += label_gap + len(step_rows) * 36 + 10
    height = top + 92

    img = Image.new("RGB", (W, height + 240), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 10], fill=color)

    draw.text((PAD, 56), "SUSPICION", font=f_brand, fill="#6B7280")
    draw.text((PAD, 92), f"{band} risk", font=f_band, fill=color)
    meta = f"{len(items)} issue{'s' if len(items) != 1 else ''} · identity {verdict.get('identity', 'not verified')}"
    draw.text((W - PAD - draw.textbbox((0, 0), meta, font=f_meta)[2], 122), meta, font=f_meta, fill="#6B7280")
    draw.line([(PAD, 186), (W - PAD, 186)], fill="#E5E7EB", width=1)

    y = 214
    draw.text((PAD, y), "WHAT STANDS OUT", font=f_label, fill="#9CA3AF")
    y += 42
    for block in wrapped:
        for line in block["head"]:
            draw.text((PAD, y), line, font=f_head, fill="#111827")
            y += 42
        for line in block["quote"]:
            draw.text((PAD, y), line, font=f_quote, fill="#4B5563")
            y += 34
        for line in block["act"]:
            draw.text((PAD, y), line, font=f_act, fill="#111827")
            y += 34
        y += gap

    if check_rows:
        draw.text((PAD, y), "CHECKS RUN", font=f_label, fill="#9CA3AF")
        y += 38
        for row in check_rows:
            draw.text((PAD, y), row, font=f_check, fill="#374151")
            y += 34
        y += 16

    draw.text((PAD, y), "DO NOW", font=f_label, fill="#9CA3AF")
    y += 38
    for row in step_rows:
        draw.text((PAD, y), row, font=f_check, fill="#111827")
        y += 36

    y += 26
    draw.line([(PAD, y), (W - PAD, y)], fill="#E5E7EB", width=1)
    draw.text((PAD, y + 18), "Risk signals, not a verdict.", font=f_meta, fill="#9CA3AF")
    width = draw.textbbox((0, 0), BOT_HANDLE, font=f_meta)[2]
    draw.text((W - PAD - width, y + 18), BOT_HANDLE, font=f_meta, fill="#6B7280")

    img = img.crop((0, 0, W, y + 58))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
