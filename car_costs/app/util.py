"""Lecture des champs de formulaire et formatage à la suisse."""

from calendar import monthrange
from datetime import date


def parse_money(value: str | None) -> int | None:
    """« 1'234,50 » ou « 1234.5 » -> 123450 centimes. Vide -> None."""
    if value is None:
        return None
    text = value.strip().replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        amount = round(float(text) * 100)
    except ValueError:
        raise ValueError(f"Montant invalide : {value}") from None
    return amount


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().replace("'", "").replace(" ", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"Nombre invalide : {value}") from None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"Nombre invalide : {value}") from None


def parse_date(value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"Date invalide : {value}") from None


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def format_money(cents: int | float | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    return sign + f"{abs(cents) / 100:,.2f}".replace(",", "’")


def format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def format_km(km: int | float | None) -> str:
    if km is None:
        return ""
    return f"{round(km):,}".replace(",", "’")


def add_months(start: date, months: int) -> date:
    year, month = divmod(start.month - 1 + months, 12)
    year += start.year
    month += 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))
