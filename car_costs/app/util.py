"""Lecture des champs de formulaire et formatage à la suisse."""

import csv
import io
import re
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


def parse_any_date(value: str | None) -> date | None:
    """ISO (2025-03-01, éventuellement avec l'heure) ou suisse (01.03.2025, 1/3/2025)."""
    text = (value or "").strip()
    if not text:
        return None
    if match := re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        year, month, day = (int(g) for g in match.groups())
    elif match := re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text):
        day, month, year = (int(g) for g in match.groups())
        year += 2000 if year < 100 else 0
    else:
        raise ValueError(f"Date invalide : {value}")
    return date(year, month, day)


def read_csv(text: str) -> list[list[str]]:
    """Lignes non vides d'un CSV séparé par ; , ou tabulation (export Excel compris)."""
    text = text.lstrip("\ufeff")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = max(";,\t", key=first.count)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in row)]


def decode_upload(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")
