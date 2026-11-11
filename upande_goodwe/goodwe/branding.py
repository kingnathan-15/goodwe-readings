"""Builds the Solar & Water app mark: the Upande tile that briefly shows GoodWe and HydraX.

Same tile as the other Upande apps (upande_production's production_logo.svg): a white disc with
a thin dark rim and the Upande arrow where upande_logo.png has it on a 689 canvas. On load it
plays once:

    Upande arrow -> arrow shrinks into the centre -> GoodWe logo -> HydraX logo
                 -> arrow grows back out and rests as the plain Upande mark

Pure CSS, no script. With reduced motion only the resting mark is shown. The desk tile is
replayed on hover by public/js/upande_goodwe_tile_logo.js; the plant web page does the same
for its header logo. Logo files are embedded as data URIs because an SVG shown through <img>
can't load external images.

Logo files, in upande_goodwe/public/images/logos/:
    upande.svg                 the Upande arrow (copied from upande_production's upande_mark.svg)
    goodwe.svg | goodwe.png    official GoodWe logo  (skipped if missing)
    hydrax.svg | hydrax.png    official HydraX logo  (skipped if missing)

Rebuild after changing them:
    python -m upande_goodwe.goodwe.branding        # from apps/upande_goodwe
    bench build --app upande_goodwe

Output: public/images/upande-goodwe-icon.svg (also copied to upande-goodwe-logo.svg for the page).
"""

import base64
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(os.path.dirname(HERE), "public", "images")
LOGOS = os.path.join(IMAGES, "logos")

PARTNERS = ("goodwe", "hydrax")
MIME = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# Timing (seconds)
REST_START = 0.8      # Upande shown before the arrow leaves
ARROW_MOVE = 0.5      # arrow shrinking in / growing back
PARTNER_SHOW = 1.3    # each partner logo, including its fades
FADE = 0.3
EASE = "cubic-bezier(.3,.7,.2,1)"

# Canvas and placement, matching upande_production
SIZE = 689
DISC = (344.5, 344.5, 340)
ARROW_BOX = (169, 165, 359, 364)          # x, y, w, h of the arrow on the canvas
ARROW_ORIGIN = "348.5px 347px"
PARTNER_BOX = (94.5, 244.5, 500, 200)     # where a partner logo sits inside the disc

# Fallback arrow if logos/upande.svg is missing: the geometry of upande_mark.svg.
FALLBACK_ARROW = (
    '<path fill="#625d59" d="M0 0 H272 V85 H0 Z"/>'
    '<path fill="#45433f" d="M272 0 H359 V364 H272 Z"/>'
    '<path fill="#1a1917" d="M246 85 H272 V161 L246 187 Z"/>'
    '<path fill="#45433f" d="M198 85 H246 V187 L69 364 H0 V283 Z"/>'
)


def _find(name):
    for ext in (".svg", ".png", ".webp", ".jpg", ".jpeg"):
        path = os.path.join(LOGOS, name + ext)
        if os.path.exists(path):
            return path
    return None


def _data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        return f"data:{MIME[ext]};base64," + base64.b64encode(fh.read()).decode()


def _arrow_markup():
    """Inner markup of logos/upande.svg, scaled into ARROW_BOX."""
    x, y, w, h = ARROW_BOX
    path = os.path.join(LOGOS, "upande.svg")
    if not os.path.exists(path):
        return f'<g transform="translate({x} {y})">{FALLBACK_ARROW}</g>'
    text = open(path, encoding="utf-8").read()
    vb = re.search(r'viewBox="([\d.\s-]+)"', text)
    vx, vy, vw, vh = (float(n) for n in vb.group(1).split()) if vb else (0, 0, w, h)
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", text, flags=re.S)
    inner = re.sub(r"<!--.*?-->|<title>.*?</title>", "", inner, flags=re.S)
    inner = re.sub(r">\s+<", "><", inner).strip()
    scale = min(w / vw, h / vh)
    return f'<g transform="translate({x} {y}) scale({scale:g}) translate({-vx:g} {-vy:g})">{inner}</g>'


def _pct(t, total):
    return f"{max(0, min(100, t / total * 100)):.2f}%"


def build_svg():
    partners = [(name, _data_uri(p)) for name in PARTNERS if (p := _find(name))]
    n = len(partners)
    if n:
        total = REST_START + ARROW_MOVE + n * PARTNER_SHOW + ARROW_MOVE + 0.3
    else:
        total = 0

    css = [
        ".arrow{transform-box:view-box;transform-origin:%s}" % ARROW_ORIGIN,
        ".p{opacity:0}",
    ]
    if n:
        out_end = REST_START + ARROW_MOVE
        back_start = out_end + n * PARTNER_SHOW - 0.1
        back_end = back_start + ARROW_MOVE
        css.append(
            "@keyframes arrow{"
            f"0%,{_pct(REST_START, total)}{{transform:scale(1);opacity:1}}"
            f"{_pct(out_end, total)},{_pct(back_start, total)}{{transform:scale(0);opacity:0}}"
            f"{_pct(back_end, total)},100%{{transform:scale(1);opacity:1}}}}"
            f".arrow{{animation:arrow {total:.2f}s {EASE} 1 both}}"
        )
        for i in range(n):
            start = out_end - 0.15 + i * PARTNER_SHOW
            end = start + PARTNER_SHOW
            css.append(
                f"@keyframes p{i}{{0%,{_pct(start, total)}{{opacity:0;transform:scale(.92)}}"
                f"{_pct(start + FADE, total)},{_pct(end - FADE, total)}{{opacity:1;transform:scale(1)}}"
                f"{_pct(end, total)},100%{{opacity:0;transform:scale(1.04)}}}}"
                f".p{i}{{transform-box:view-box;transform-origin:50% 50%;"
                f"animation:p{i} {total:.2f}s {EASE} 1 both}}"
            )
        css.append("@media (prefers-reduced-motion:reduce){.arrow,.p{animation:none!important}.p{opacity:0}}")

    px, py, pw, ph = PARTNER_BOX
    partner_markup = "".join(
        f'<image class="p p{i}" href="{uri}" x="{px}" y="{py}" width="{pw}" height="{ph}" '
        f'preserveAspectRatio="xMidYMid meet"/>'
        for i, (name, uri) in enumerate(partners)
    )
    cx, cy, r = DISC
    label = "Upande" + "".join(f", {name.capitalize() if name != 'goodwe' else 'GoodWe'}" for name, _ in partners)
    label = label.replace("Hydrax", "HydraX")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" '
        f'role="img" aria-label="{label}">'
        "<!-- Generated by upande_goodwe/goodwe/branding.py; edit there. Plays once on load and rests "
        "as the plain Upande mark. -->"
        f"<title>Solar &amp; Water</title><style>{''.join(css)}</style>"
        f'<clipPath id="ugw-disc"><circle cx="{cx}" cy="{cy}" r="{r - 4}"/></clipPath>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#ffffff" stroke="#1c1b19" stroke-opacity=".3" stroke-width="7"/>'
        f'<g clip-path="url(#ugw-disc)">{partner_markup}</g>'
        f'<g class="arrow">{_arrow_markup()}</g>'
        "</svg>"
    ), [name for name, _ in partners], total


def build_logo():
    os.makedirs(IMAGES, exist_ok=True)
    svg, partners, total = build_svg()
    written = []
    for filename in ("upande-goodwe-icon.svg", "upande-goodwe-logo.svg"):
        path = os.path.join(IMAGES, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        written.append(path)
    return {"partners": partners, "duration_s": round(total, 2), "written": written}


if __name__ == "__main__":
    print(build_logo())
