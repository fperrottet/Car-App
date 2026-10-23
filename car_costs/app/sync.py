"""Imports automatiques : odomètre depuis Home Assistant, sessions depuis EVCC."""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChargingSession, OdometerReading, Setting, Vehicle

MILES_TO_KM = 1.609344
# Le recorder de HA garde 10 jours d'historique par défaut.
HISTORY_DAYS = 10


def fetch_json(url: str, token: str | None = None, timeout: float = 20):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def home_assistant() -> tuple[str, str] | None:
    """Adresse de l'API HA et jeton : celui du Supervisor dans l'add-on,
    ou HA_URL / HA_TOKEN pour le développement."""
    if token := os.environ.get("SUPERVISOR_TOKEN"):
        return "http://supervisor/core/api", token
    if (url := os.environ.get("HA_URL")) and (token := os.environ.get("HA_TOKEN")):
        return url.rstrip("/") + "/api", token
    return None


def local_naive(value: str) -> datetime:
    """Horodatage ISO (souvent UTC) -> heure locale sans fuseau."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def to_km(value, unit: str | None) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None  # « unavailable », « unknown »...
    if unit and unit.lower() in ("mi", "miles"):
        number *= MILES_TO_KM
    return round(number)


def sync_odometer(session: Session, vehicle: Vehicle, ha: tuple[str, str], now: datetime) -> int:
    """Garde un relevé par jour (le plus haut) issu de l'historique HA."""
    base, token = ha
    entity = vehicle.odometer_entity
    state = fetch_json(f"{base}/states/{urllib.parse.quote(entity)}", token)
    unit = (state.get("attributes") or {}).get("unit_of_measurement")

    start = (now - timedelta(days=HISTORY_DAYS)).astimezone().isoformat()
    query = urllib.parse.urlencode({"filter_entity_id": entity, "minimal_response": "", "no_attributes": ""})
    history = fetch_json(f"{base}/history/period/{urllib.parse.quote(start)}?{query}", token)

    per_day: dict = {}
    rows = (history[0] if history else []) + [state]
    for row in rows:
        km = to_km(row.get("state"), unit)
        stamp = row.get("last_changed") or row.get("last_updated")
        if km is None or not stamp:
            continue
        day = local_naive(stamp).date()
        per_day[day] = max(km, per_day.get(day, 0))

    existing = {
        r.date: r
        for r in session.scalars(
            select(OdometerReading).where(OdometerReading.vehicle_id == vehicle.id, OdometerReading.source == "ha")
        )
    }
    for day, km in per_day.items():
        if day in existing:
            existing[day].km = max(existing[day].km, km)
        else:
            session.add(OdometerReading(vehicle_id=vehicle.id, date=day, km=km, source="ha"))
    return len(per_day)


def sync_evcc(session: Session, vehicle: Vehicle) -> int:
    """Importe ou met à jour les sessions de charge d'EVCC."""
    data = fetch_json(vehicle.evcc_url.rstrip("/") + "/api/sessions")
    if isinstance(data, dict):  # anciennes versions : {"result": [...]}
        data = data.get("result") or []
    wanted = (vehicle.evcc_vehicle or "").strip().lower()

    existing = {s.evcc_id: s for s in session.scalars(select(ChargingSession))}
    count = 0
    for raw in data:
        if wanted and (raw.get("vehicle") or "").strip().lower() != wanted:
            continue
        energy = raw.get("chargedEnergy") or 0
        if not raw.get("id") or not raw.get("created") or energy <= 0:
            continue
        finished = raw.get("finished")
        item = existing.get(raw["id"]) or ChargingSession(evcc_id=raw["id"])
        item.vehicle_id = vehicle.id
        item.started = local_naive(raw["created"])
        item.finished = local_naive(finished) if finished and not finished.startswith("0001") else None
        item.loadpoint = raw.get("loadpoint")
        item.evcc_vehicle = raw.get("vehicle")
        item.energy_kwh = float(energy)
        item.cost_cents = round(raw["price"] * 100) if raw.get("price") is not None else None
        item.price_per_kwh = raw.get("pricePerKWh")
        item.solar_pct = raw.get("solarPercentage")
        item.odometer_km = round(raw["odometer"]) if raw.get("odometer") else None
        session.add(item)
        count += 1
    return count


def set_setting(session: Session, key: str, value: str | None) -> None:
    item = session.get(Setting, key) or Setting(key=key)
    item.value = value
    session.add(item)


def get_settings(session: Session) -> dict[str, str | None]:
    return {s.key: s.value for s in session.scalars(select(Setting))}


def run_sync(session: Session, now: datetime | None = None, ha: tuple[str, str] | None = None) -> None:
    """Lance les deux imports et note le résultat de chacun."""
    now = now or datetime.now()
    vehicle = session.scalars(select(Vehicle).order_by(Vehicle.id)).first()
    if vehicle is None:
        return
    ha = ha or home_assistant()
    stamp = now.strftime("%d.%m.%Y %H:%M")

    def attempt(key: str, job, noun: str) -> None:
        try:
            count = job()
            session.commit()
            set_setting(session, key, f"OK le {stamp} : {count} {noun}")
        except Exception as exc:  # réseau, JSON, entité inconnue...
            session.rollback()
            set_setting(session, key, f"Erreur le {stamp} : {exc}")
        session.commit()

    if not vehicle.odometer_entity:
        set_setting(session, "sync_odometer", "non configuré")
    elif ha is None:
        set_setting(session, "sync_odometer", "pas d'accès à Home Assistant (hors add-on)")
    else:
        attempt("sync_odometer", lambda: sync_odometer(session, vehicle, ha, now), "jours de relevés")

    if not vehicle.evcc_url:
        set_setting(session, "sync_evcc", "non configuré")
    else:
        attempt("sync_evcc", lambda: sync_evcc(session, vehicle), "sessions")
    session.commit()
