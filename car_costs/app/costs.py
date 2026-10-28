"""Calculs : échéances des contrats, coût total, CHF/km, pneus, économies et rappels."""

import bisect
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    PERIODS,
    Category,
    ChargingSession,
    Contract,
    ElectricityTariff,
    Expense,
    Inspection,
    Maintenance,
    OdometerReading,
    Reminder,
    Vehicle,
)
from .util import add_months, format_date, format_km


HOME_CHARGING = "Recharge à domicile (EVCC)"


def due_dates(contract: Contract, until: date) -> list[date]:
    """Échéances d'un contrat jusqu'à `until` (incluse) et avant sa fin."""
    if not contract.amount_cents or contract.period not in PERIODS:
        return []
    step = PERIODS[contract.period]
    dates = []
    i = 0
    while True:
        due = add_months(contract.start_date, i * step)
        if due > until or (contract.end_date and due >= contract.end_date):
            return dates
        dates.append(due)
        i += 1


def due_amount(contract: Contract, due: date) -> int:
    """Montant d'une échéance : au prorata des jours couverts si le contrat
    se termine avant l'échéance suivante (date de fin incluse)."""
    amount = contract.amount_cents or 0
    following = add_months(due, PERIODS[contract.period])
    if contract.end_date is None or contract.end_date >= following:
        return amount
    covered = (contract.end_date - due).days + 1
    return min(amount, round(amount * covered / (following - due).days))


def sync_contract_expenses(session: Session, contract: Contract, today: date) -> None:
    """Crée les échéances manquantes d'un contrat sous forme de dépenses."""
    existing = set(
        session.scalars(
            select(Expense.date).where(Expense.contract_id == contract.id, Expense.generated.is_(True))
        )
    )
    for due in due_dates(contract, today):
        if due in existing:
            continue
        session.add(
            Expense(
                vehicle_id=contract.vehicle_id,
                date=due,
                amount_cents=due_amount(contract, due),
                category_id=contract.category_id,
                vendor=contract.provider,
                description=f"{contract.kind} {contract.period}"
                + (" (prorata)" if due_amount(contract, due) != contract.amount_cents else ""),
                contract_id=contract.id,
                generated=True,
            )
        )


def sync_all_contracts(session: Session, today: date) -> None:
    for contract in session.scalars(select(Contract)):
        sync_contract_expenses(session, contract, today)
    session.commit()


def odometer_points(session: Session, vehicle: Vehicle) -> list[tuple[date, int]]:
    """Tous les relevés connus : achat, relevés, entretiens et dépenses."""
    points: list[tuple[date, int]] = []
    if vehicle.purchase_date and vehicle.purchase_odometer_km is not None:
        points.append((vehicle.purchase_date, vehicle.purchase_odometer_km))
    readings = session.scalars(select(OdometerReading).where(OdometerReading.vehicle_id == vehicle.id))
    points += [(r.date, r.km) for r in readings]
    for model in (Maintenance, Expense):
        rows = session.scalars(
            select(model).where(model.vehicle_id == vehicle.id, model.odometer_km.is_not(None))
        )
        points += [(r.date, r.odometer_km) for r in rows]
    sessions = session.scalars(
        select(ChargingSession).where(
            ChargingSession.vehicle_id == vehicle.id, ChargingSession.odometer_km.is_not(None)
        )
    )
    points += [(s.started.date(), s.odometer_km) for s in sessions]
    return sorted(points)


def current_km(session: Session, vehicle: Vehicle) -> int | None:
    points = odometer_points(session, vehicle)
    return max(km for _, km in points) if points else None


def km_between(points: list[tuple[date, int]], start: date, end: date) -> int | None:
    """Km parcourus entre deux dates, d'après les relevés disponibles."""
    before = [km for d, km in points if d <= start]
    inside = [km for d, km in points if start <= d <= end]
    first = max(before) if before else (min(inside) if inside else None)
    last_candidates = [km for d, km in points if d <= end]
    if first is None or not last_candidates:
        return None
    last = max(last_candidates)
    return last - first if last > first else None


def depreciation(vehicle: Vehicle, start: date, end: date) -> int:
    """Part linéaire de la perte de valeur sur [start, end], en centimes."""
    if not vehicle.purchase_date or not vehicle.purchase_price_cents or vehicle.depreciation_years <= 0:
        return 0
    loss = vehicle.purchase_price_cents - (vehicle.residual_value_cents or 0)
    if loss <= 0:
        return 0
    dep_end = add_months(vehicle.purchase_date, 12 * vehicle.depreciation_years)
    lo = max(start, vehicle.purchase_date)
    hi = min(end + timedelta(days=1), dep_end)
    if hi <= lo:
        return 0
    return round(loss * (hi - lo).days / (dep_end - vehicle.purchase_date).days)


@dataclass
class Summary:
    start: date
    end: date
    total_cents: int = 0
    fixed_cents: int = 0
    variable_cents: int = 0
    depreciation_cents: int = 0
    km: int | None = None
    charging_cents: int = 0  # recharges à domicile, estimation d'avant EVCC comprise
    estimated_cents: int = 0
    energy_kwh: float = 0.0
    fuel_liters: float = 0.0
    fuel_cents: int = 0
    by_category: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def months(self) -> float:
        return max((self.end - self.start).days + 1, 1) / 30.4375

    @property
    def total_with_depreciation(self) -> int:
        return self.total_cents + self.depreciation_cents

    @property
    def per_month_cents(self) -> float:
        return self.total_with_depreciation / self.months

    @property
    def per_km_cents(self) -> float | None:
        return self.total_with_depreciation / self.km if self.km else None

    @property
    def kwh_per_100km(self) -> float | None:
        return 100 * self.energy_kwh / self.km if self.km and self.energy_kwh else None

    @property
    def liters_per_100km(self) -> float | None:
        return 100 * self.fuel_liters / self.km if self.km and self.fuel_liters else None


def summarize(session: Session, vehicle: Vehicle, start: date, end: date, estimate=False) -> Summary:
    """`estimate` : estimation déjà calculée (None si aucune) ; False pour la calculer."""
    summary = Summary(start=start, end=end)
    expenses = session.scalars(
        select(Expense).where(Expense.vehicle_id == vehicle.id, Expense.date >= start, Expense.date <= end)
    )
    totals: dict[tuple[str, str], int] = {}
    for expense in expenses:
        name = expense.category.name if expense.category else "Sans catégorie"
        kind = expense.category.kind if expense.category else "variable"
        totals[(name, kind)] = totals.get((name, kind), 0) + expense.amount_cents
        summary.total_cents += expense.amount_cents
        if expense.fuel_liters:
            summary.fuel_liters += expense.fuel_liters
            summary.fuel_cents += expense.amount_cents
        if kind == "fixe":
            summary.fixed_cents += expense.amount_cents
        else:
            summary.variable_cents += expense.amount_cents
    charges = list(
        session.scalars(
            select(ChargingSession).where(
                ChargingSession.vehicle_id == vehicle.id,
                ChargingSession.started >= datetime.combine(start, time.min),
                ChargingSession.started < datetime.combine(end + timedelta(days=1), time.min),
            )
        )
    )
    if charges:
        summary.energy_kwh = sum(c.energy_kwh for c in charges)
        summary.charging_cents = sum(c.cost_cents or 0 for c in charges)
        totals[(HOME_CHARGING, "variable")] = totals.get((HOME_CHARGING, "variable"), 0) + summary.charging_cents
        summary.total_cents += summary.charging_cents
        summary.variable_cents += summary.charging_cents
    if estimate is False:
        estimate = charging_estimate(session, vehicle, end)
    if estimate is not None:
        _, kwh, cents = estimate.total(start, end)
        if cents:
            summary.estimated_cents = cents
            summary.energy_kwh += kwh
            summary.charging_cents += cents
            totals[(ESTIMATED_CHARGING, "variable")] = cents
            summary.total_cents += cents
            summary.variable_cents += cents
    summary.by_category = sorted(((n, k, c) for (n, k), c in totals.items()), key=lambda row: -row[2])
    summary.depreciation_cents = depreciation(vehicle, start, end)
    summary.km = km_between(odometer_points(session, vehicle), start, end)
    return summary


# ---------------------------------------------------------------- recharges d'avant EVCC

ESTIMATED_CHARGING = "Recharge estimée (avant EVCC)"
# Pertes entre la prise et la batterie : la consommation de l'ordinateur de bord
# ne les compte pas (même hypothèse que dans le tableau de Fred : / 0,9).
CHARGING_EFFICIENCY = 0.9
DEFAULT_KWH_PER_100KM = 15.0
# Au-delà, une consommation relevée ne vaut pas pour toute la période écoulée
# (ordinateur de bord remis à zéro peu avant le relevé).
CONSUMPTION_MAX_DAYS = 62


@dataclass
class EstimateDay:
    km: float
    kwh_per_100km: float
    kwh: float  # à la prise, pertes comprises
    cents: float


@dataclass
class ChargingEstimate:
    start: date
    end: date  # exclu : première session EVCC (ou fin de la période demandée)
    days: dict[date, EstimateDay] = field(default_factory=dict)

    def total(self, start: date, end: date) -> tuple[float, float, int]:
        """km, kWh et centimes estimés entre start et end (inclus)."""
        rows = [d for day, d in self.days.items() if start <= day <= end]
        return sum(d.km for d in rows), sum(d.kwh for d in rows), round(sum(d.cents for d in rows))


def daily_km(points: list[tuple[date, int]]) -> list[tuple[date, date, float]]:
    """Segments (début, fin exclue, km par jour) entre relevés successifs."""
    best: dict[date, int] = {}
    for d, km in points:
        best[d] = max(km, best.get(d, 0))
    ordered = sorted(best.items())
    segments = []
    for (d0, k0), (d1, k1) in zip(ordered, ordered[1:]):
        if k1 > k0:
            segments.append((d0, d1, (k1 - k0) / (d1 - d0).days))
    return segments


def charging_estimate(session: Session, vehicle: Vehicle, today: date) -> ChargingEstimate | None:
    """Coût des recharges à domicile d'avant EVCC, jour par jour : km du jour
    (interpolés entre relevés) × consommation de la période × prix du kWh."""
    if vehicle.estimate_charging is False or not vehicle.purchase_date or not vehicle.plugs_in:
        return None
    sessions = list(
        session.scalars(
            select(ChargingSession).where(ChargingSession.vehicle_id == vehicle.id).order_by(ChargingSession.started)
        )
    )
    end = sessions[0].started.date() if sessions else today + timedelta(days=1)
    if end <= vehicle.purchase_date:
        return None
    tariffs = [
        (t.start_date, t.price_per_kwh)
        for t in session.scalars(
            select(ElectricityTariff).where(ElectricityTariff.vehicle_id == vehicle.id).order_by(ElectricityTariff.start_date)
        )
    ]
    paid = [s for s in sessions if s.cost_cents is not None and s.energy_kwh]
    evcc_price = sum(s.cost_cents for s in paid) / 100 / sum(s.energy_kwh for s in paid) if paid else None
    if not tariffs and evcc_price is None:
        return None

    points = odometer_points(session, vehicle)
    readings = sorted(
        (r.date, r.kwh_per_100km)
        for r in session.scalars(
            select(OdometerReading).where(
                OdometerReading.vehicle_id == vehicle.id, OdometerReading.kwh_per_100km.is_not(None)
            )
        )
    )
    # Repli : moyenne des relevés du même mois de l'année, puis de tous les relevés.
    by_month: dict[int, list[float]] = {}
    for d, value in readings:
        by_month.setdefault(d.month, []).append(value)
    overall = sum(v for _, v in readings) / len(readings) if readings else DEFAULT_KWH_PER_100KM
    reading_dates = [d for d, _ in readings]

    def consumption(day: date) -> float:
        i = bisect.bisect_right(reading_dates, day)  # premier relevé qui couvre ce jour
        if i < len(readings) and (reading_dates[i] - day).days <= CONSUMPTION_MAX_DAYS:
            return readings[i][1]
        values = by_month.get(day.month)
        return sum(values) / len(values) if values else overall

    def price(day: date) -> float:
        active = [p for start, p in tariffs if start <= day]
        if active:
            return active[-1]
        return evcc_price if evcc_price is not None else tariffs[0][1]

    estimate = ChargingEstimate(vehicle.purchase_date, end)
    for d0, d1, per_day in daily_km(points):
        day = max(d0, vehicle.purchase_date)
        while day < min(d1, end):
            cons = consumption(day)
            kwh = per_day * cons / 100 / CHARGING_EFFICIENCY
            estimate.days[day] = EstimateDay(per_day, cons, kwh, kwh * price(day) * 100)
            day += timedelta(days=1)
    return estimate if estimate.days else None


# ---------------------------------------------------------------- expertise

# Canton de Fribourg : 5 ans après la 1re immatriculation, puis 3 ans, puis tous les 2 ans.
DEFAULT_INSPECTION_INTERVALS = [5, 3, 2]


def inspection_intervals(vehicle: Vehicle) -> list[int]:
    parts = re.split(r"[,;\s]+", vehicle.inspection_intervals or "")
    values = [int(p) for p in parts if p.isdigit() and int(p) > 0]
    return values or DEFAULT_INSPECTION_INTERVALS


def next_inspection(session: Session, vehicle: Vehicle) -> date | None:
    """Date de la prochaine expertise, depuis la dernière (ou la 1re immatriculation)."""
    done = list(
        session.scalars(select(Inspection.date).where(Inspection.vehicle_id == vehicle.id).order_by(Inspection.date))
    )
    base = done[-1] if done else vehicle.first_registration_date
    if base is None:
        return None
    steps = inspection_intervals(vehicle)
    count = len(done)
    if done and vehicle.first_registration_date:
        # Les expertises du précédent propriétaire ne sont souvent pas saisies :
        # on déduit le rang de la dernière depuis l'âge de la voiture (± 1 an).
        age = (base - vehicle.first_registration_date).days / 365.25
        count, total = 0, 0
        while True:
            total += steps[min(count, len(steps) - 1)]
            if total > age + 1:
                break
            count += 1
        count = max(count, 1)
    return add_months(base, 12 * steps[min(count, len(steps) - 1)])


# ---------------------------------------------------------------- pneus


def km_at(points: list[tuple[date, int]], day: date) -> int | None:
    """Compteur estimé à une date : interpolé entre les relevés qui l'entourent."""
    before = [(d, km) for d, km in points if d <= day]
    after = [(d, km) for d, km in points if d > day]
    if not before:
        return None
    d0, k0 = max(before, key=lambda p: (p[1], p[0]))
    later = [(d, km) for d, km in after if km >= k0]
    if not later:
        return k0
    d1, k1 = min(later)
    return round(k0 + (k1 - k0) * (day - d0).days / (d1 - d0).days) if d1 > d0 else k0


# ---------------------------------------------------------------- économies face à l'essence

# Catégories de dépenses comptées comme « carburant » de la voiture électrique.
ENERGY_CATEGORIES = ("Recharge externe", "Recharge à domicile")


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def fuel_price(index: dict[str, float], vehicle: Vehicle, month: date) -> float | None:
    """Prix de l'essence (CHF/l) d'un mois : le prix de référence saisi,
    corrigé par l'évolution de l'indice national depuis sa date."""
    if not vehicle.fuel_price_ref:
        return None
    keys = sorted(index)
    if not keys:
        return vehicle.fuel_price_ref

    def value(day: date) -> float:
        earlier = [k for k in keys if k <= month_key(day)]
        return index[earlier[-1] if earlier else keys[0]]

    reference = value(vehicle.fuel_price_ref_date) if vehicle.fuel_price_ref_date else index[keys[-1]]
    return vehicle.fuel_price_ref * value(month) / reference


@dataclass
class FuelMonth:
    start: date
    km: int | None
    price: float | None  # CHF/l
    petrol_cents: int  # ce qu'aurait coûté l'essence
    electric_cents: int  # recharges EVCC + recharges saisies en dépenses

    @property
    def saved_cents(self) -> int:
        return self.petrol_cents - self.electric_cents


def savings(session: Session, vehicle: Vehicle, index: dict[str, float], today: date) -> list[FuelMonth]:
    """Mois par mois : coût de l'électricité contre celui de l'essence pour les mêmes km."""
    if vehicle.energy_name != "électrique" or not vehicle.fuel_consumption_l100 or not vehicle.fuel_price_ref:
        return []
    points = odometer_points(session, vehicle)
    firsts = [d for d in (vehicle.purchase_date, points[0][0] if points else None) if d]
    if not firsts:
        return []
    electric: dict[str, int] = {}
    for c in session.scalars(select(ChargingSession).where(ChargingSession.vehicle_id == vehicle.id)):
        key = month_key(c.started)
        electric[key] = electric.get(key, 0) + (c.cost_cents or 0)
    expenses = session.scalars(
        select(Expense)
        .join(Category, Expense.category_id == Category.id)
        .where(Expense.vehicle_id == vehicle.id, Category.name.in_(ENERGY_CATEGORIES))
    )
    for e in expenses:
        electric[month_key(e.date)] = electric.get(month_key(e.date), 0) + e.amount_cents
    estimate = charging_estimate(session, vehicle, today)
    for day, row in (estimate.days.items() if estimate else []):
        electric[month_key(day)] = electric.get(month_key(day), 0) + row.cents

    month = date(min(firsts).year, min(firsts).month, 1)
    rows: list[FuelMonth] = []
    while month <= today:
        end = min(add_months(month, 1) - timedelta(days=1), today)
        km = km_between(points, month, end)
        price = fuel_price(index, vehicle, month)
        petrol = round((km or 0) * vehicle.fuel_consumption_l100 / 100 * price * 100) if price else 0
        rows.append(FuelMonth(month, km, price, petrol, round(electric.get(month_key(month), 0))))
        month = add_months(month, 1)
    return rows


# ---------------------------------------------------------------- rappels

# Seuils de profondeur de sculpture conseillés (le minimum légal est 1,6 mm).
TREAD_ADVISED_MM = {"été": 3.0, "hiver": 4.0, "4 saisons": 4.0}


@dataclass
class Alert:
    when: date
    text: str
    overdue: bool


def reminders(session: Session, vehicle: Vehicle, today: date) -> list[Alert]:
    """Échéances des 90 prochains jours (et celles dépassées non traitées)."""
    horizon = today + timedelta(days=90)
    items: list[Alert] = []
    for c in session.scalars(select(Contract).where(Contract.vehicle_id == vehicle.id, Contract.end_date.is_not(None))):
        if c.end_date < today:
            continue
        if c.notice_days:
            notice = c.end_date - timedelta(days=c.notice_days)
            if today <= notice <= horizon:
                items.append(Alert(notice, f"Dernier délai de résiliation : {c.kind} {c.provider}", False))
        if c.end_date <= horizon:
            items.append(Alert(c.end_date, f"Fin du contrat : {c.kind} {c.provider}", False))

    km_now = current_km(session, vehicle)
    latest = session.scalars(
        select(Maintenance).where(Maintenance.vehicle_id == vehicle.id).order_by(Maintenance.date.desc())
    ).first()
    if latest:
        if latest.next_date and latest.next_date <= horizon:
            items.append(
                Alert(latest.next_date, f"Prochain service prévu le {format_date(latest.next_date)}", latest.next_date < today)
            )
        if latest.next_km and km_now is not None and latest.next_km - km_now <= 1500:
            left = latest.next_km - km_now
            text = (
                f"Service dépassé de {format_km(-left)} km" if left < 0 else f"Service dans {format_km(left)} km"
            ) + f" (prévu à {format_km(latest.next_km)} km)"
            items.append(Alert(today, text, left < 0))

    inspection = next_inspection(session, vehicle)
    if inspection and inspection <= today + timedelta(days=120):
        items.append(Alert(inspection, f"Expertise à prévoir (échéance le {format_date(inspection)})", inspection < today))

    for r in session.scalars(select(Reminder).where(Reminder.vehicle_id == vehicle.id, Reminder.done_date.is_(None))):
        if r.due_date and r.due_date <= horizon:
            items.append(Alert(r.due_date, r.title, r.due_date < today))
        elif r.due_km and km_now is not None and r.due_km - km_now <= 1500:
            items.append(Alert(today, f"{r.title} (à {format_km(r.due_km)} km)", km_now >= r.due_km))

    from . import tires  # tires importe costs

    pairs = [p for p in tires.vehicle_pairs(session, vehicle) if not p.retired_date]
    mounted = {p.tire_set.season for p in pairs if any(q.end_date is None for q in p.periods)}
    if "été" in mounted and today.month in (10, 11):
        items.append(Alert(today, "Pneus d'été montés : penser aux pneus d'hiver (d'octobre à Pâques)", False))
    if "hiver" in mounted and (3, 15) <= (today.month, today.day) <= (5, 15):
        items.append(Alert(today, "Pneus d'hiver montés : repasser aux pneus d'été", False))
    for p in pairs:
        tread = tires.last_tread(p)
        if tread is not None and tread < TREAD_ADVISED_MM.get(p.tire_set.season, 3.0):
            items.append(Alert(today, f"Pneus {p.label} : {tread:.1f} mm mesurés, à remplacer bientôt", tread < 1.6))
    return sorted(items, key=lambda r: r.when)

@dataclass
class Month:
    start: date
    km: int | None
    cost_cents: int
    per_km_cents: float | None  # cumulé depuis le début de la série


def monthly(session: Session, vehicle: Vehicle, today: date) -> list[Month]:
    """Une ligne par mois, de l'achat (ou de la première donnée) à aujourd'hui."""
    points = odometer_points(session, vehicle)
    firsts = [d for d in (vehicle.purchase_date, session.scalar(select(func.min(Expense.date)).where(Expense.vehicle_id == vehicle.id))) if d]
    if points:
        firsts.append(points[0][0])
    if not firsts:
        return []
    month = date(min(firsts).year, min(firsts).month, 1)
    origin = month
    months: list[Month] = []
    cumulative = 0
    estimate = charging_estimate(session, vehicle, today)
    while month <= today:
        end = min(add_months(month, 1) - timedelta(days=1), today)
        summary = summarize(session, vehicle, month, end, estimate)
        cumulative += summary.total_with_depreciation
        km_total = km_between(points, origin, end)
        months.append(
            Month(month, summary.km, summary.total_with_depreciation, cumulative / km_total if km_total else None)
        )
        month = add_months(month, 1)
    return months


# ---------------------------------------------------------------- statistiques batterie

# Capacité utile d'une Citigo e iV / e-Up (32,3 kWh nets sur 36,8 kWh bruts),
# utilisée tant que la voiture ne fournit pas sa propre mesure.
DEFAULT_USABLE_KWH = 32.3


def battery_stats(session: Session, vehicle: Vehicle, today: date, capacity_kwh: float | None = None) -> dict:
    """Recharges depuis l'achat : nombre, énergie, cycles complets équivalents."""
    sessions = list(
        session.scalars(
            select(ChargingSession).where(ChargingSession.vehicle_id == vehicle.id).order_by(ChargingSession.started)
        )
    )
    charged = sum(s.energy_kwh for s in sessions)
    solar = sum(s.energy_kwh * (s.solar_pct or 0) / 100 for s in sessions)
    paid = [s for s in sessions if s.cost_cents is not None]
    estimate = charging_estimate(session, vehicle, today)
    estimated_kwh = sum(d.kwh for d in estimate.days.values()) if estimate else 0.0
    total = charged + estimated_kwh
    usable = capacity_kwh or DEFAULT_USABLE_KWH
    since = vehicle.purchase_date or (sessions[0].started.date() if sessions else None)
    km = km_between(odometer_points(session, vehicle), since, today) if since else None
    return {
        "charges": len(sessions),
        "first_charge": sessions[0].started.date().isoformat() if sessions else None,
        "charged_kwh": round(charged, 1),
        "solar_pct": round(100 * solar / charged, 1) if charged else None,
        "avg_price_kwh": round(sum(s.cost_cents for s in paid) / 100 / sum(s.energy_kwh for s in paid), 4)
        if paid and sum(s.energy_kwh for s in paid)
        else None,
        "estimated_kwh": round(estimated_kwh, 1),
        "total_kwh": round(total, 1),
        "usable_kwh": usable,
        # Énergie arrivée dans la batterie (pertes de charge déduites) / capacité utile.
        "cycles": round(total * CHARGING_EFFICIENCY / usable, 1) if total else 0,
        "since": since.isoformat() if since else None,
        "km": km,
        "kwh_per_100km": round(100 * total / km, 1) if km and total else None,
    }


# ---------------------------------------------------------------- carburant


@dataclass
class FuelFill:
    expense: Expense
    km: int | None  # compteur au moment du plein
    distance: int | None  # depuis le plein précédent
    liters_per_100km: float | None

    @property
    def price_per_liter(self) -> float | None:
        return self.expense.amount_cents / 100 / self.expense.fuel_liters if self.expense.fuel_liters else None


def fuel_log(session: Session, vehicle: Vehicle) -> list[FuelFill]:
    """Pleins du plus récent au plus ancien. Chaque plein est supposé complet :
    ses litres couvrent les km roulés depuis le plein précédent."""
    expenses = list(
        session.scalars(
            select(Expense)
            .where(Expense.vehicle_id == vehicle.id, Expense.fuel_liters.is_not(None))
            .order_by(Expense.date, Expense.id)
        )
    )
    fills: list[FuelFill] = []
    previous_km: int | None = None
    for e in expenses:
        distance = e.odometer_km - previous_km if e.odometer_km and previous_km and e.odometer_km > previous_km else None
        fills.append(FuelFill(e, e.odometer_km, distance, 100 * e.fuel_liters / distance if distance else None))
        if e.odometer_km:
            previous_km = e.odometer_km
    return fills[::-1]


def fuel_average(fills: list[FuelFill]) -> float | None:
    """Consommation moyenne sur les pleins dont la distance est connue."""
    measured = [f for f in fills if f.distance]
    distance = sum(f.distance for f in measured)
    return 100 * sum(f.expense.fuel_liters for f in measured) / distance if distance else None
