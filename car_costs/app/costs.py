"""Calculs : échéances des contrats, coût total, CHF/km et rappels."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PERIODS, Contract, Expense, Maintenance, OdometerReading, Vehicle
from .util import add_months, format_date, format_km


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
                amount_cents=contract.amount_cents,
                category_id=contract.category_id,
                vendor=contract.provider,
                description=f"{contract.kind} {contract.period}",
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


def summarize(session: Session, vehicle: Vehicle, start: date, end: date) -> Summary:
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
        if kind == "fixe":
            summary.fixed_cents += expense.amount_cents
        else:
            summary.variable_cents += expense.amount_cents
    summary.by_category = sorted(((n, k, c) for (n, k), c in totals.items()), key=lambda row: -row[2])
    summary.depreciation_cents = depreciation(vehicle, start, end)
    summary.km = km_between(odometer_points(session, vehicle), start, end)
    return summary


@dataclass
class Reminder:
    when: date
    text: str
    overdue: bool


def reminders(session: Session, vehicle: Vehicle, today: date) -> list[Reminder]:
    """Échéances des 90 prochains jours (et celles dépassées non traitées)."""
    horizon = today + timedelta(days=90)
    items: list[Reminder] = []
    for c in session.scalars(select(Contract).where(Contract.vehicle_id == vehicle.id, Contract.end_date.is_not(None))):
        if c.end_date < today:
            continue
        if c.notice_days:
            notice = c.end_date - timedelta(days=c.notice_days)
            if today <= notice <= horizon:
                items.append(Reminder(notice, f"Dernier délai de résiliation : {c.kind} {c.provider}", False))
        if c.end_date <= horizon:
            items.append(Reminder(c.end_date, f"Fin du contrat : {c.kind} {c.provider}", False))

    latest = session.scalars(
        select(Maintenance).where(Maintenance.vehicle_id == vehicle.id).order_by(Maintenance.date.desc())
    ).first()
    if latest:
        if latest.next_date and latest.next_date <= horizon:
            items.append(
                Reminder(latest.next_date, f"Prochain service prévu le {format_date(latest.next_date)}", latest.next_date < today)
            )
        km_now = current_km(session, vehicle)
        if latest.next_km and km_now is not None and latest.next_km - km_now <= 1500:
            left = latest.next_km - km_now
            text = (
                f"Service dépassé de {format_km(-left)} km" if left < 0 else f"Service dans {format_km(left)} km"
            ) + f" (prévu à {format_km(latest.next_km)} km)"
            items.append(Reminder(today, text, left < 0))
    return sorted(items, key=lambda r: r.when)
