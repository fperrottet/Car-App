"""Graphiques SVG rendus côté serveur (aucune dépendance JavaScript)."""

import math
from html import escape

from .costs import Month
from .util import format_km

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

WIDTH = 960
LEFT, RIGHT = 64, 16
TOP_H, BOTTOM_H, GAP = 190, 150, 44


def nice_ticks(high: float, count: int = 4) -> list[float]:
    if high <= 0:
        return [0]
    raw = high / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    return [i * step for i in range(int(math.ceil(high / step)) + 1)]


def cost_and_km_chart(months: list[Month]) -> str:
    """Deux panneaux sur le même axe des mois : coût par km cumulé (ligne)
    et km parcourus par mois (barres)."""
    if not months:
        return ""
    n = len(months)
    plot_w = WIDTH - LEFT - RIGHT
    slot = plot_w / n
    x = lambda i: LEFT + slot * (i + 0.5)  # noqa: E731

    top_y0 = 28
    bottom_y0 = top_y0 + TOP_H + GAP
    height = bottom_y0 + BOTTOM_H + 40
    parts = [
        f'<svg viewBox="0 0 {WIDTH} {height}" role="img" class="chart" '
        f'aria-label="Coût par km cumulé et km parcourus par mois">'
    ]

    # Panneau du haut : CHF/km cumulé.
    values = [(i, m.per_km_cents / 100) for i, m in enumerate(months) if m.per_km_cents is not None]
    ticks = nice_ticks(max((v for _, v in values), default=0) * 1.1 or 1)
    top_max = ticks[-1] or 1
    ty = lambda v: top_y0 + TOP_H * (1 - v / top_max)  # noqa: E731
    parts.append(f'<text x="{LEFT}" y="16" class="chart-title">Coût par km depuis le début (CHF/km)</text>')
    for t in ticks:
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{ty(t):.1f}" y2="{ty(t):.1f}" class="grid"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{ty(t) + 4:.1f}" class="tick" text-anchor="end">{t:.2f}</text>')
    if values:
        path = " ".join(f"{'M' if k == 0 else 'L'}{x(i):.1f} {ty(v):.1f}" for k, (i, v) in enumerate(values))
        parts.append(f'<path d="{path}" class="line"/>')
        for i, v in values:
            label = f"{MONTHS[months[i].start.month - 1]} {months[i].start.year} : {v:.2f} CHF/km"
            parts.append(f'<circle cx="{x(i):.1f}" cy="{ty(v):.1f}" r="3.5" class="dot"><title>{escape(label)}</title></circle>')
        i, v = values[-1]
        parts.append(f'<text x="{x(i) - 6:.1f}" y="{ty(v) - 10:.1f}" class="value" text-anchor="end">{v:.2f}</text>')
    else:
        parts.append(f'<text x="{LEFT + plot_w / 2}" y="{top_y0 + TOP_H / 2}" class="tick" text-anchor="middle">'
                     "Pas encore assez de relevés du compteur</text>")

    # Panneau du bas : km par mois.
    kms = [m.km or 0 for m in months]
    ticks = nice_ticks(max(kms) * 1.1 or 1)
    bottom_max = ticks[-1] or 1
    by = lambda v: bottom_y0 + BOTTOM_H * (1 - v / bottom_max)  # noqa: E731
    parts.append(f'<text x="{LEFT}" y="{bottom_y0 - 12}" class="chart-title">Km parcourus par mois</text>')
    for t in ticks:
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{by(t):.1f}" y2="{by(t):.1f}" class="grid"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{by(t) + 4:.1f}" class="tick" text-anchor="end">{format_km(t)}</text>')
    bar_w = max(min(slot * 0.7, 36), 2)
    for i, m in enumerate(months):
        if not m.km:
            continue
        label = f"{MONTHS[m.start.month - 1]} {m.start.year} : {format_km(m.km)} km"
        parts.append(
            f'<rect x="{x(i) - bar_w / 2:.1f}" y="{by(m.km):.1f}" width="{bar_w:.1f}" '
            f'height="{bottom_y0 + BOTTOM_H - by(m.km):.1f}" rx="2" class="bar-km"><title>{escape(label)}</title></rect>'
        )

    # Mois sous le panneau du bas (au plus ~12 étiquettes).
    every = max(1, math.ceil(n / 12))
    base_y = bottom_y0 + BOTTOM_H
    for i, m in enumerate(months):
        if i % every:
            continue
        text = MONTHS[m.start.month - 1] if m.start.month != 1 and i else f"{MONTHS[m.start.month - 1]} {m.start.year}"
        parts.append(f'<text x="{x(i):.1f}" y="{base_y + 18}" class="tick" text-anchor="middle">{escape(text)}</text>')
    parts.append("</svg>")
    return "".join(parts)
