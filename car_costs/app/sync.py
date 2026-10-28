"""Imports automatiques (odomètre et batterie depuis Home Assistant, sessions
d'EVCC, indice du prix de l'essence) et capteurs renvoyés à Home Assistant."""

import csv
import io
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import costs
from . import tires as tire_pairs
from .models import AXLES, BatterySnapshot, ChargingSession, OdometerReading, Setting, Vehicle

MILES_TO_KM = 1.609344
# Le recorder de HA garde 10 jours d'historique par défaut.
HISTORY_DAYS = 10


# Indice des prix de l'essence de l'OFEN (base décembre 2020 = 100), mensuel.
FUEL_INDEX_URL = "https://www.uvek-gis.admin.ch/BFE/ogd/106/ogd106_preise_benzin.csv"


def fetch_text(url: str, token: str | None = None, timeout: float = 20, accept: str = "*/*") -> str:
    request = urllib.request.Request(url, headers={"Accept": accept})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def fetch_json(url: str, token: str | None = None, timeout: float = 20):
    return json.loads(fetch_text(url, token, timeout, accept="application/json"))


def post_json(url: str, payload: dict, token: str | None = None, timeout: float = 20) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout):
        pass


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


def vehicle_matches(wanted: str, name: str | None) -> bool:
    """Filtre souple : EVCC enregistre le titre du véhicule (« Skoda Citigo »)
    alors qu'on connaît souvent son nom de config (« citigo »). Une session
    sans véhicule identifié est gardée."""
    name = (name or "").strip().lower()
    return not wanted or not name or wanted in name or name in wanted


def sync_evcc(session: Session, vehicle: Vehicle) -> str:
    """Importe ou met à jour les sessions de charge d'EVCC."""
    data = fetch_json(vehicle.evcc_url.rstrip("/") + "/api/sessions")
    if isinstance(data, dict):  # anciennes versions : {"result": [...]}
        if "result" not in data:
            raise ValueError(f"réponse inattendue d'EVCC : {str(data)[:200]}")
        data = data["result"] or []
    wanted = (vehicle.evcc_vehicle or "").strip().lower()

    existing = {s.evcc_id: s for s in session.scalars(select(ChargingSession))}
    kept = 0
    others: set[str] = set()
    for raw in data:
        if not vehicle_matches(wanted, raw.get("vehicle")):
            others.add(raw.get("vehicle"))
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
        kept += 1

    message = f"{kept} sessions importées sur {len(data)} reçues d'EVCC"
    if others:
        names = ", ".join(sorted(f"« {n} »" for n in others))
        message += f" ; ignorées car d'un autre véhicule que « {vehicle.evcc_vehicle} » : {names}"
    return message


# ---------------------------------------------------------------- prix de l'essence


def parse_fuel_index(text: str) -> dict[str, float]:
    """CSV de l'OFEN (« Datum,Preis_LIK_indexiert ») -> {"2025-11": 119.0}."""
    lines = text.strip().splitlines()
    if not lines:
        return {}
    delimiter = ";" if ";" in lines[0] else ","
    index: dict[str, float] = {}
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        match = re.match(r"\s*(\d{4})-(\d{2})", row[0]) if len(row) >= 2 else None
        if not match:
            continue  # en-tête ou ligne vide
        try:
            index[f"{match[1]}-{match[2]}"] = float(row[1].replace(",", "."))
        except ValueError:
            continue
    return index


def load_fuel_index(session: Session) -> dict[str, float]:
    item = session.get(Setting, "fuel_index")
    try:
        return json.loads(item.value) if item and item.value else {}
    except ValueError:
        return {}


def sync_fuel_index(session: Session, today: date) -> str:
    """Télécharge l'indice au plus une fois par jour (il est mensuel)."""
    fetched = session.get(Setting, "fuel_index_date")
    index = load_fuel_index(session)
    if not (index and fetched and fetched.value == today.isoformat()):
        index = parse_fuel_index(fetch_text(FUEL_INDEX_URL))
        if not index:
            raise ValueError("fichier de l'OFEN vide ou illisible")
        set_setting(session, "fuel_index", json.dumps(index, sort_keys=True))
        set_setting(session, "fuel_index_date", today.isoformat())
    last = max(index)
    return f"indice OFEN de {min(index)} à {last} ({index[last]:g} points)"


# ---------------------------------------------------------------- santé de la batterie (OVMS)

# Métriques OVMS de la VW e-Up / Skoda Citigo e iV, telles qu'elles
# apparaissent dans les identifiants d'entités HA (points remplacés par _).
CELL_PATTERNS = ("xvu_b_c_soh", "b_c_soh", "cell_soh")
SOH_PATTERNS = ("xvu_b_soh_charge", "soh_charge", "v_b_soh", "xvu_b_soh_range", "soh")
MODULE_PATTERN = "hist_soh_mod"
# Capacité utile estimée par la voiture (kWh), la plus précise en premier.
CAPACITY_PATTERNS = ("xvu_b_cap_kwh_norm", "cap_kwh_norm", "xvu_b_cap_kwh_abs", "cap_kwh_abs", "usable_capacity", "battery_capacity")
CAPACITY_RANGE = (5.0, 120.0)
SOH_RANGE = (40.0, 110.0)


def numbers(value) -> list[float]:
    """Valeurs numériques d'un état ou d'un attribut (nombre, liste, « 95.1,94.8 »)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in numbers(v)]
    if isinstance(value, str) and re.fullmatch(r"[\s\[\]\d.,;\-]+", value):
        return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", value)]
    return []


def plausible(values: list[float]) -> bool:
    return bool(values) and all(SOH_RANGE[0] <= v <= SOH_RANGE[1] for v in values)


def entity_values(state: dict) -> list[float]:
    """La plus longue liste plausible de valeurs SoH dans l'état ou les attributs
    (HA limite l'état à 255 caractères, un tableau peut donc être en attribut)."""
    candidates = [numbers(state.get("state"))]
    candidates += [numbers(v) for v in (state.get("attributes") or {}).values()]
    candidates = [c for c in candidates if plausible(c)]
    return max(candidates, key=len, default=[])


def discover_battery(states: dict[str, dict], vehicle: Vehicle) -> dict:
    """Trouve les entités SoH (globale, par cellule, historique des modules)."""
    keys = {eid: eid.lower().replace(".", "_") for eid in states}
    soh_ids = [e for e in keys if "soh" in keys[e] and "tire" not in keys[e]]

    def pick(patterns, exclude=()) -> str | None:
        for pattern in patterns:
            for eid in soh_ids:
                if pattern in keys[eid] and not any(x in keys[eid] for x in exclude):
                    return eid
        return None

    cells = vehicle.cell_soh_entity or pick(CELL_PATTERNS, exclude=(MODULE_PATTERN,))
    soh = vehicle.soh_entity or pick(SOH_PATTERNS, exclude=(MODULE_PATTERN, "b_c_soh", "cell"))
    modules = sorted(e for e in soh_ids if MODULE_PATTERN in keys[e])
    capacity = next(
        (eid for pattern in CAPACITY_PATTERNS for eid in sorted(keys) if pattern in keys[eid] and "hist" not in keys[eid]),
        None,
    )
    return {"soh": soh, "cells": cells, "modules": modules, "capacity": capacity, "candidates": sorted(soh_ids)}


def capacity_value(state: dict) -> float | None:
    values = numbers(state.get("state"))
    if len(values) == 1 and CAPACITY_RANGE[0] <= values[0] <= CAPACITY_RANGE[1]:
        return values[0]
    return None


def sync_battery(session: Session, vehicle: Vehicle, ha: tuple[str, str], today: date) -> str:
    base, token = ha
    states = {s["entity_id"]: s for s in fetch_json(f"{base}/states", token) if isinstance(s, dict) and "entity_id" in s}
    found = discover_battery(states, vehicle)
    soh_values = entity_values(states.get(found["soh"]) or {})
    soh = soh_values[0] if len(soh_values) == 1 else None
    cells = entity_values(states.get(found["cells"]) or {})
    modules = {eid: entity_values(states[eid]) for eid in found["modules"]}
    capacity = capacity_value(states.get(found["capacity"]) or {})
    seen = [(eid, str(states[eid].get("state"))[:60]) for eid in found["candidates"] + ([found["capacity"]] if found["capacity"] else [])]
    set_setting(session, "battery_entities", json.dumps({**found, "seen": seen}), vehicle)
    set_setting(session, "battery_modules", json.dumps(modules), vehicle)
    if soh is None and not cells and capacity is None:
        return f"aucune valeur de SoH trouvée ({len(seen)} entités contenant « soh »)"

    snapshot = session.scalars(
        select(BatterySnapshot).where(BatterySnapshot.vehicle_id == vehicle.id, BatterySnapshot.date == today)
    ).first() or BatterySnapshot(vehicle_id=vehicle.id, date=today)
    snapshot.soh = soh
    snapshot.cells = json.dumps(cells) if cells else None
    snapshot.capacity_kwh = capacity
    session.add(snapshot)
    parts = [f"SoH {soh:g} %" if soh is not None else "SoH global absent"]
    if cells:
        parts.append(f"{len(cells)} cellules (min {min(cells):g} %)")
    if capacity is not None:
        parts.append(f"capacité {capacity:g} kWh")
    return ", ".join(parts)


# ---------------------------------------------------------------- capteurs envoyés à HA

SENSOR_PREFIX = "sensor.frais_voiture_"


def ha_sensors(session: Session, vehicle: Vehicle, today: date) -> dict[str, tuple]:
    """Capteurs publiés dans HA : nom -> (état, unité, nom affiché, icône, attributs)."""
    costs.sync_all_contracts(session, today)
    since = vehicle.purchase_date or date(today.year, 1, 1)
    month = costs.summarize(session, vehicle, today.replace(day=1), today)
    year = costs.summarize(session, vehicle, date(today.year, 1, 1), today)
    total = costs.summarize(session, vehicle, since, today)
    sensors: dict[str, tuple] = {
        "mois": (round(month.total_with_depreciation / 100, 2), "CHF", "Frais voiture ce mois", "mdi:cash", {}),
        "annee": (round(year.total_with_depreciation / 100, 2), "CHF", "Frais voiture cette année", "mdi:cash-multiple", {}),
    }
    if total.per_km_cents is not None:
        sensors["par_km"] = (
            round(total.per_km_cents / 100, 3), "CHF/km", "Frais voiture par km", "mdi:counter",
            {"depuis": since.isoformat(), "km": total.km},
        )
    rows = costs.savings(session, vehicle, load_fuel_index(session), today)
    if rows:
        saved = sum(r.saved_cents for r in rows)
        sensors["economie_essence"] = (
            round(saved / 100, 2), "CHF", "Économie face à l'essence", "mdi:gas-station-off",
            {"depuis": rows[0].start.isoformat()},
        )
    alerts = costs.reminders(session, vehicle, today)
    if alerts:
        first = alerts[0]
        sensors["prochain_rappel"] = (
            first.text[:250], None, "Frais voiture : prochain rappel", "mdi:calendar-alert",
            {"date": first.when.isoformat(), "en_retard": first.overdue, "nombre": len(alerts)},
        )
    if vehicle.fuelled:
        fills = costs.fuel_log(session, vehicle)
        if fills:
            last = fills[0]
            average = costs.fuel_average(fills)
            sensors["carburant"] = (
                round(average, 2) if average else None, "L/100 km", "Consommation de carburant", "mdi:gas-station",
                {"last_fill": last.expense.date.isoformat(), "last_liters": last.expense.fuel_liters,
                 "last_price_per_liter": round(last.price_per_liter, 3) if last.price_per_liter else None,
                 "fills": len(fills), "year_liters": round(year.fuel_liters, 1), "year_cents": year.fuel_cents},
            )
    if vehicle.plugs_in:
        battery_sensors(session, vehicle, today, sensors)
    vehicle_sensor(session, vehicle, today, sensors, alerts)
    return sensors


def battery_sensors(session: Session, vehicle: Vehicle, today: date, sensors: dict) -> None:
    snapshots = list(
        session.scalars(
            select(BatterySnapshot).where(BatterySnapshot.vehicle_id == vehicle.id).order_by(BatterySnapshot.date.desc())
        )
    )
    latest = snapshots[0] if snapshots else None
    cells = json.loads(latest.cells) if latest and latest.cells else []
    if cells:
        sensors["cellule_min"] = (
            min(cells), "%", "Batterie : cellule la plus faible", "mdi:battery-heart-variant",
            {"moyenne": round(sum(cells) / len(cells), 2), "max": max(cells), "cellule": cells.index(min(cells)) + 1},
        )
    # Pour la carte Lovelace : SoH, cellules, capacité et historiques (au plus 1 point par semaine).
    history: list = []
    capacity_history: list = []
    for snap in reversed(snapshots[:730]):
        snap_cells = json.loads(snap.cells) if snap.cells else []
        value = snap.soh if snap.soh is not None else (sum(snap_cells) / len(snap_cells) if snap_cells else None)
        for series, point in ((history, value), (capacity_history, snap.capacity_kwh)):
            if point is not None and (not series or (snap.date - date.fromisoformat(series[-1][0])).days >= 7):
                series.append([snap.date.isoformat(), round(point, 2)])
    soh = None
    if latest:
        soh = latest.soh if latest.soh is not None else (round(sum(cells) / len(cells), 2) if cells else None)
    capacity = next((s.capacity_kwh for s in snapshots if s.capacity_kwh is not None), None)
    stats = costs.battery_stats(session, vehicle, today, capacity)
    sensors["batterie"] = (
        soh, "%", "Batterie : état de santé", "mdi:battery-heart",
        {
            "date": latest.date.isoformat() if latest else None,
            "cells": cells,
            "history": history,
            "capacity_kwh": capacity,
            "capacity_history": capacity_history,
            **stats,
        },
    )


def vehicle_sensor(session: Session, vehicle: Vehicle, today: date, sensors: dict, alerts) -> None:
    points = costs.odometer_points(session, vehicle)
    km_now = costs.current_km(session, vehicle)
    mounted = [
        (period, pair)
        for pair in tire_pairs.vehicle_pairs(session, vehicle)
        for period in pair.periods
        if period.end_date is None and not pair.retired_date
    ]
    tires = [
        {"season": pair.tire_set.season, "label": pair.label, "size": pair.tire_set.size,
         "position": AXLES[period.axle].lower(), "count": 2,
         "km": tire_pairs.pair_km(pair, points, km_now), "tread_mm": tire_pairs.last_tread(pair)}
        for period, pair in sorted(mounted, key=lambda m: m[0].axle != "avant")
    ]
    inspection = costs.next_inspection(session, vehicle)
    sensors["vehicule"] = (
        costs.current_km(session, vehicle), "km", vehicle.name, "mdi:car-electric" if vehicle.plugs_in else "mdi:car",
        {
            "plate": vehicle.plate,
            "energy": vehicle.energy_name,
            "connected": vehicle.is_connected,
            "model": " ".join(p for p in (vehicle.make, vehicle.model) if p) or None,
            "purchase_date": vehicle.purchase_date.isoformat() if vehicle.purchase_date else None,
            "next_inspection": inspection.isoformat() if inspection else None,
            "tires": tires,
            "alerts": [{"date": a.when.isoformat(), "text": a.text, "overdue": a.overdue} for a in alerts[:6]],
        },
    )


def push_sensors(session: Session, vehicle: Vehicle, ha: tuple[str, str], today: date) -> str:
    base, token = ha
    sensors = ha_sensors(session, vehicle, today)
    prefix = sensor_prefix(session, vehicle)
    for name, (state, unit, label, icon, extra) in sensors.items():
        if prefix != SENSOR_PREFIX and not label.startswith(vehicle.name):
            label = f"{vehicle.name} : {label}"  # distinguer les voitures dans HA
        attributes = {"friendly_name": label, "icon": icon, **extra}
        if unit:
            attributes["unit_of_measurement"] = unit
        state = "unknown" if state is None else state  # HA refuse un état vide
        post_json(f"{base}/states/{prefix}{name}", {"state": state, "attributes": attributes}, token)
    return f"{len(sensors)} capteurs {prefix}* mis à jour"


def set_setting(session: Session, key: str, value: str | None, vehicle: Vehicle | None = None) -> None:
    """`vehicle` : réglage propre à une voiture (état de ses imports)."""
    key = f"{key}@{vehicle.id}" if vehicle else key
    item = session.get(Setting, key) or Setting(key=key)
    item.value = value
    session.add(item)


def get_settings(session: Session, vehicle: Vehicle | None = None) -> dict[str, str | None]:
    """Réglages communs, remplacés par ceux de la voiture donnée."""
    settings = {}
    suffix = f"@{vehicle.id}" if vehicle else None
    for item in session.scalars(select(Setting)):
        if "@" not in item.key:
            settings.setdefault(item.key, item.value)
        elif suffix and item.key.endswith(suffix):
            settings[item.key.removesuffix(suffix)] = item.value
    return settings


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def sensor_prefix(session: Session, vehicle: Vehicle) -> str:
    """La première voiture garde sensor.frais_voiture_*, les autres ajoutent leur nom."""
    first = session.scalar(select(func.min(Vehicle.id)))
    if vehicle.id == first:
        return SENSOR_PREFIX
    return f"{SENSOR_PREFIX}{slug(vehicle.name) or vehicle.id}_"


def run_sync(session: Session, now: datetime | None = None, ha: tuple[str, str] | None = None) -> None:
    """Lance les imports de chaque voiture, publie les capteurs et note le résultat."""
    now = now or datetime.now()
    vehicles = list(session.scalars(select(Vehicle).where(Vehicle.archived.is_not(True)).order_by(Vehicle.id)))
    if not vehicles:
        return
    ha = ha or home_assistant()
    stamp = now.strftime("%d.%m.%Y %H:%M")

    def attempt(key: str, job, vehicle: Vehicle | None) -> None:
        try:
            result = job()
            session.commit()
            set_setting(session, key, f"OK le {stamp} : {result}", vehicle)
        except Exception as exc:  # réseau, JSON, entité inconnue...
            session.rollback()
            set_setting(session, key, f"Erreur le {stamp} : {exc}", vehicle)
        session.commit()

    # L'indice de l'essence est commun : une fois si une voiture électrique s'y compare.
    if not any(v.energy_name == "électrique" and v.fuel_consumption_l100 and v.fuel_price_ref for v in vehicles):
        set_setting(session, "sync_fuel", "non configuré")
    else:
        attempt("sync_fuel", lambda: sync_fuel_index(session, now.date()), None)

    for vehicle in vehicles:
        sync_vehicle(session, vehicle, now, ha, lambda key, job, v=vehicle: attempt(key, job, v))
    session.commit()


def sync_vehicle(session: Session, vehicle: Vehicle, now: datetime, ha, attempt) -> None:
    def skip(key: str, why: str) -> None:
        set_setting(session, key, why, vehicle)

    offline = "voiture non connectée"
    if not vehicle.is_connected:
        skip("sync_odometer", offline)
    elif not vehicle.odometer_entity:
        skip("sync_odometer", "non configuré")
    elif ha is None:
        skip("sync_odometer", "pas d'accès à Home Assistant (hors add-on)")
    else:
        attempt("sync_odometer", lambda: f"{sync_odometer(session, vehicle, ha, now)} jours de relevés")

    if not vehicle.plugs_in:
        skip("sync_evcc", "pas de recharge")
    elif not vehicle.is_connected:
        skip("sync_evcc", offline)
    elif not vehicle.evcc_url:
        skip("sync_evcc", "non configuré")
    else:
        attempt("sync_evcc", lambda: sync_evcc(session, vehicle))

    if ha is None:
        for key in ("sync_battery", "sync_ha_sensors"):
            skip(key, "pas d'accès à Home Assistant (hors add-on)")
        return
    if not vehicle.plugs_in:
        skip("sync_battery", "pas de batterie de traction")
    elif not vehicle.is_connected:
        skip("sync_battery", offline)
    else:
        attempt("sync_battery", lambda: sync_battery(session, vehicle, ha, now.date()))
    # En dernier, pour publier des valeurs à jour (aussi pour une voiture non connectée).
    attempt("sync_ha_sensors", lambda: push_sensors(session, vehicle, ha, now.date()))
