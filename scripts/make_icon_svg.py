"""Erzeugt die TRAVLR-Icons als SVG.

Dunkles Quadrat (#151515), flache Formen: grauer Startpunkt, weiße Flugbahn,
orangefarbenes Flugzeug (Stufen grau → weiß → orange).

    python3 scripts/make_icon_svg.py        # schreibt app/static/icon.svg + scripts/icon-*.svg
    swift scripts/make-icons.swift          # rendert daraus die PNGs
"""
from pathlib import Path

BG, GREY, WHITE, ORANGE = "#151515", "#3d3d3d", "#ffffff", "#f47b20"
ROOT = Path(__file__).resolve().parent.parent

# Flugbahn: quadratische Bézierkurve, endet in Flugrichtung 45° nach rechts oben
P0, C, P1 = (128, 386), (172, 338), (252, 258)


def bez(t):
    return tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * c + t ** 2 * b for a, c, b in zip(P0, C, P1))


def sub(t0, t1):
    """Teilstück der Kurve als neue quadratische Kurve (für einzelne Striche)."""
    a, b = bez(t0), bez(t1)
    # Kontrollpunkt des Teilstücks: Schnitt der Tangenten, hier über die Ableitung
    def d(t):
        return tuple(2 * (1 - t) * (c - p) + 2 * t * (q - c) for p, c, q in zip(P0, C, P1))
    da = d(t0)
    k = (t1 - t0) / 2
    c = (a[0] + da[0] * k, a[1] + da[1] * k)
    return f"M{a[0]:.1f} {a[1]:.1f}Q{c[0]:.1f} {c[1]:.1f} {b[0]:.1f} {b[1]:.1f}"


# Flugzeug von oben, Spitze nach oben, um (0,0); danach gedreht und verschoben
PLANE = ("M0 -92C9 -92 15 -82 15 -70V-22L86 18V40L15 20V54L36 70V84L0 74L-36 84V70L-15 54V20"
         "L-86 40V18L-15 -22V-70C-15 -82 -9 -92 0 -92Z")


def content(scale=1.0):
    s = scale
    tr = f"translate(256 256) scale({s}) translate(-256 -256)"
    gx, gy = bez(0)
    return f"""<g transform="{tr}">
    <circle cx="{gx:.1f}" cy="{gy:.1f}" r="22" fill="{GREY}"/>
    <path d="{sub(0.30, 0.56)}" stroke="{WHITE}" stroke-width="24" stroke-linecap="round" fill="none"/>
    <path d="{sub(0.74, 1.0)}" stroke="{WHITE}" stroke-width="24" stroke-linecap="round" fill="none"/>
    <path d="{PLANE}" transform="translate(330 180) rotate(45) scale(1.0)" fill="{ORANGE}" stroke="{ORANGE}" stroke-width="6" stroke-linejoin="round"/>
  </g>"""


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n  {body}\n</svg>\n'


# "any": abgerundetes Quadrat auf transparentem Grund
any_svg = svg(f'<rect width="512" height="512" rx="112" fill="{BG}"/>\n  ' + content())
# "maskable"/Apple: randlos, Motiv etwas kleiner (sichere Zone ~80 %)
full_svg = svg(f'<rect width="512" height="512" fill="{BG}"/>\n  ' + content(0.86))

(ROOT / "app/static/icon.svg").write_text(any_svg)
(ROOT / "scripts/icon-any.svg").write_text(any_svg)
(ROOT / "scripts/icon-full.svg").write_text(full_svg)
print("SVGs geschrieben")
