"""Pages ajoutées en 0.3 : pneus, batterie, rappels administratifs (dont
l'expertise) et économies par rapport à une voiture à essence."""

import json
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import charts, costs, sync
from .models import (
    TIRE_POSITIONS,
    TIRE_SEASONS,
    BatterySnapshot,
    Category,
    Expense,
    Inspection,
    Reminder,
    TireMount,
    TireSet,
    Vehicle,
)
from .util import add_months, clean, parse_date, parse_float, parse_int, parse_money


def linked_expense(session: Session, vehicle: Vehicle, expense_id: int | None, amount: int | None, category: str, **fields):
    """Crée, met à jour ou supprime la dépense liée à un achat ; renvoie son id."""
    expense = session.get(Expense, expense_id) if expense_id else None
    if amount is None:
        if expense is not None:
            session.delete(expense)
        return None
    if expense is None:
        found = session.scalars(select(Category).where(Category.name == category)).first()
        expense = Expense(vehicle_id=vehicle.id, category_id=found.id if found else None)
        session.add(expense)
    expense.amount_cents = amount
    for name, value in fields.items():
        setattr(expense, name, value)
    session.flush()
    return expense.id


def positions_overlap(a: str, b: str) -> bool:
    return a == b or "les 4" in (a, b)


def reminder_presets(vehicle: Vehicle, today: date) -> dict[str, dict]:
    reg = vehicle.first_registration_date
    vignette = date(today.year, 1, 31) if today <= date(today.year, 1, 31) else date(today.year + 1, 1, 31)
    return {
        "vignette": {
            "label": "Vignette",
            "title": "Vignette autoroutière",
            "due_date": vignette,
            "repeat_months": 12,
            "notes": "La vignette d'une année reste valable jusqu'au 31 janvier de l'année suivante.",
        },
        "garantie": {
            "label": "Garantie constructeur",
            "title": "Fin de la garantie constructeur",
            "due_date": add_months(reg, 24) if reg else None,
            "notes": "Faire contrôler les petits défauts avant la fin de la garantie.",
        },
        "batterie": {
            "label": "Garantie batterie",
            "title": "Fin de la garantie batterie",
            "due_date": add_months(reg, 96) if reg else None,
            "due_km": 160000,
            "notes": "En général 8 ans ou 160'000 km, avec au moins 70 % de capacité : à vérifier sur le contrat.",
        },
        "impot": {
            "label": "Impôt cantonal",
            "title": "Impôt sur les véhicules",
            "repeat_months": 12,
            "notes": "Facture annuelle de l'Office de la circulation (OCN à Fribourg).",
        },
    }


def register(app: FastAPI, *, Sessions, render, redirect, get_vehicle, get_or_404, today) -> None:
    # ---------------------------------------------------------------- pneus

    @app.get("/tires", response_class=HTMLResponse)
    def tire_list(request: Request):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            points = costs.odometer_points(session, vehicle)
            km_now = costs.current_km(session, vehicle)
            sets = list(
                session.scalars(
                    select(TireSet).where(TireSet.vehicle_id == vehicle.id).order_by(TireSet.purchase_date.desc())
                )
            )
            rows = [
                {"set": s, "km": costs.tire_km(s, points, km_now), "tread": costs.last_tread(s)}
                for s in sets
            ]
            return render(
                request,
                "tires.html",
                mounted=[r for r in rows if r["set"].current_mount and not r["set"].retired_date],
                stored=[r for r in rows if not r["set"].current_mount and not r["set"].retired_date],
                retired=[r for r in rows if r["set"].retired_date],
                today=today(),
                km_now=km_now,
            )

    def tire_form(request: Request, session: Session, item: TireSet | None, form=None, error=None):
        vehicle = get_vehicle(session)
        last = session.scalars(select(TireSet).order_by(TireSet.id.desc())).first()
        points = costs.odometer_points(session, vehicle) if vehicle else []
        km_now = costs.current_km(session, vehicle) if vehicle else None
        mounts = []
        if item and item.id:
            mounts = [(m, costs.mount_km(m, points, km_now)) for m in item.mounts]
        return render(
            request,
            "tire_form.html",
            item=item,
            mounts=mounts,
            last=last,
            seasons=TIRE_SEASONS,
            positions=TIRE_POSITIONS,
            today=today(),
            km_now=km_now,
            form=form or {},
            error=error,
        )

    async def tire_save(request: Request, item_id: int | None):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            item = get_or_404(session, TireSet, item_id) if item_id else TireSet(vehicle_id=vehicle.id)
            try:
                item.season = form.get("season") if form.get("season") in TIRE_SEASONS else TIRE_SEASONS[0]
                item.brand = clean(form.get("brand"))
                item.model = clean(form.get("model"))
                item.size = clean(form.get("size"))
                item.load_speed = clean(form.get("load_speed"))
                item.dot = clean(form.get("dot"))
                item.count = parse_int(form.get("count")) or 4
                item.position = form.get("position") if form.get("position") in TIRE_POSITIONS else TIRE_POSITIONS[0]
                if item.count == 4:
                    item.position = "les 4"
                item.purchase_date = parse_date(form.get("purchase_date"))
                item.notes = clean(form.get("notes"))
                amount = parse_money(form.get("amount"))
                session.add(item)
                session.flush()
                item.expense_id = linked_expense(
                    session,
                    vehicle,
                    item.expense_id,
                    amount,
                    "Pneus",
                    date=item.purchase_date or today(),
                    vendor=clean(form.get("vendor")),
                    description=f"{item.count} pneus {item.season} {item.label}"[:200],
                )
                if form.get("mount_now") and not item.mounts:
                    mount(session, vehicle, item, parse_date(form.get("mount_date")) or item.purchase_date or today(),
                          parse_int(form.get("mount_km")))
            except ValueError as exc:
                session.rollback()
                return tire_form(request, session, item if item.id else None, dict(form), str(exc))
            session.commit()
        return redirect(request, "/tires")

    def mount(session: Session, vehicle: Vehicle, item: TireSet, when: date, km: int | None) -> None:
        """Monte un jeu ; ceux qui occupaient la même place sont démontés."""
        km = km if km is not None else costs.km_at(costs.odometer_points(session, vehicle), when)
        others = session.scalars(select(TireSet).where(TireSet.vehicle_id == vehicle.id, TireSet.id != item.id))
        for other in others:
            current = other.current_mount
            if current and positions_overlap(other.position, item.position):
                current.removed_date, current.removed_km = when, km
        item.mounts.append(TireMount(mounted_date=when, mounted_km=km))

    @app.get("/tires/new", response_class=HTMLResponse)
    def tire_new(request: Request):
        with Sessions() as session:
            return tire_form(request, session, None)

    @app.post("/tires/new")
    async def tire_create(request: Request):
        return await tire_save(request, None)

    @app.get("/tires/{item_id}", response_class=HTMLResponse)
    def tire_edit(request: Request, item_id: int):
        with Sessions() as session:
            return tire_form(request, session, get_or_404(session, TireSet, item_id))

    @app.post("/tires/{item_id}")
    async def tire_update(request: Request, item_id: int):
        return await tire_save(request, item_id)

    @app.post("/tires/{item_id}/mount")
    async def tire_mount(request: Request, item_id: int):
        form = await request.form()
        with Sessions() as session:
            item = get_or_404(session, TireSet, item_id)
            try:
                when = parse_date(form.get("date")) or today()
                km = parse_int(form.get("km"))
            except ValueError as exc:
                return tire_form(request, session, item, dict(form), str(exc))
            if item.current_mount is None:
                mount(session, get_vehicle(session), item, when, km)
                item.retired_date = None
                session.commit()
        return redirect(request, f"/tires/{item_id}")

    @app.post("/tires/{item_id}/unmount")
    async def tire_unmount(request: Request, item_id: int):
        form = await request.form()
        with Sessions() as session:
            item = get_or_404(session, TireSet, item_id)
            current = item.current_mount
            if current is not None:
                try:
                    current.removed_date = parse_date(form.get("date")) or today()
                    current.removed_km = parse_int(form.get("km"))
                    current.tread_mm = parse_float(form.get("tread_mm"))
                except ValueError as exc:
                    session.rollback()
                    return tire_form(request, session, item, dict(form), str(exc))
                if current.removed_km is None:
                    vehicle = get_vehicle(session)
                    current.removed_km = costs.km_at(costs.odometer_points(session, vehicle), current.removed_date)
                if form.get("retire"):
                    item.retired_date = current.removed_date
                session.commit()
        return redirect(request, f"/tires/{item_id}")

    @app.post("/tires/{item_id}/tread")
    async def tire_tread(request: Request, item_id: int):
        """Sculpture mesurée après coup (jeu démonté automatiquement, contrôle au garage)."""
        form = await request.form()
        with Sessions() as session:
            item = get_or_404(session, TireSet, item_id)
            try:
                tread = parse_float(form.get("tread_mm"))
            except ValueError as exc:
                return tire_form(request, session, item, dict(form), str(exc))
            if item.mounts and tread is not None:
                item.mounts[-1].tread_mm = tread
                session.commit()
        return redirect(request, f"/tires/{item_id}")

    @app.post("/tires/{item_id}/retire")
    def tire_retire(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, TireSet, item_id)
            item.retired_date = None if item.retired_date else today()
            session.commit()
        return redirect(request, "/tires")

    @app.post("/tires/mounts/{mount_id}/delete")
    def tire_mount_delete(request: Request, mount_id: int):
        with Sessions() as session:
            item = get_or_404(session, TireMount, mount_id)
            set_id = item.tire_set_id
            session.delete(item)
            session.commit()
        return redirect(request, f"/tires/{set_id}")

    @app.post("/tires/{item_id}/delete")
    def tire_delete(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, TireSet, item_id)
            if item.expense:
                session.delete(item.expense)
            session.delete(item)
            session.commit()
        return redirect(request, "/tires")

    # ---------------------------------------------------------------- batterie

    @app.get("/battery", response_class=HTMLResponse)
    def battery_page(request: Request):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            snapshots = list(
                session.scalars(
                    select(BatterySnapshot)
                    .where(BatterySnapshot.vehicle_id == vehicle.id)
                    .order_by(BatterySnapshot.date)
                )
            )
            settings = sync.get_settings(session)
            history = []
            for s in snapshots:
                cells = json.loads(s.cells) if s.cells else []
                history.append((s.date, s.soh, cells))
            latest_cells = next((cells for _, _, cells in reversed(history) if cells), [])
            soh_points = [
                (d, soh if soh is not None else sum(cells) / len(cells)) for d, soh, cells in history if soh is not None or cells
            ]
            entities = json.loads(settings.get("battery_entities") or "{}")
            modules = json.loads(settings.get("battery_modules") or "{}")
            return render(
                request,
                "battery.html",
                vehicle=vehicle,
                history=list(reversed(history)),
                cells=latest_cells,
                soh_chart=charts.line_chart(soh_points, "Santé de la batterie (SoH, %)", "%"),
                cells_chart=charts.cells_chart(latest_cells),
                entities=entities,
                modules=modules,
                status=settings,
                soh_only=all(cells == [] for _, _, cells in history),
            )

    # ---------------------------------------------------------------- rappels et expertise

    @app.get("/reminders", response_class=HTMLResponse)
    def reminder_list(request: Request, preset: str = ""):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            return reminders_page(request, session, vehicle, preset)

    def reminders_page(request: Request, session: Session, vehicle: Vehicle, preset: str = "", form=None, error=None):
        now = today()
        presets = reminder_presets(vehicle, now)
        form = form or {}
        if preset in presets and not form:
            chosen = presets[preset]
            form = {
                "title": chosen["title"],
                "due_date": chosen.get("due_date").isoformat() if chosen.get("due_date") else "",
                "due_km": str(chosen.get("due_km") or ""),
                "repeat_months": str(chosen.get("repeat_months") or ""),
                "notes": chosen.get("notes", ""),
            }
        active = list(
            session.scalars(
                select(Reminder)
                .where(Reminder.vehicle_id == vehicle.id, Reminder.done_date.is_(None))
                .order_by(Reminder.due_date.is_(None), Reminder.due_date)
            )
        )
        done = list(
            session.scalars(
                select(Reminder)
                .where(Reminder.vehicle_id == vehicle.id, Reminder.done_date.is_not(None))
                .order_by(Reminder.done_date.desc())
                .limit(10)
            )
        )
        inspections = list(
            session.scalars(
                select(Inspection).where(Inspection.vehicle_id == vehicle.id).order_by(Inspection.date.desc())
            )
        )
        expenses = {e.id: e for e in session.scalars(select(Expense).where(Expense.id.in_([i.expense_id for i in inspections])))}
        spent = {i.id: expenses[i.expense_id].amount_cents for i in inspections if i.expense_id in expenses}
        return render(
            request,
            "reminders.html",
            vehicle=vehicle,
            active=active,
            done=done,
            presets=presets,
            inspections=inspections,
            spent=spent,
            next_inspection=costs.next_inspection(session, vehicle),
            intervals=costs.inspection_intervals(vehicle),
            alerts=costs.reminders(session, vehicle, now),
            km_now=costs.current_km(session, vehicle),
            today=now,
            form=form,
            error=error,
        )

    def fill_reminder(item: Reminder, form) -> None:
        item.title = clean(form.get("title")) or ""
        if not item.title:
            raise ValueError("Le titre du rappel est obligatoire")
        item.due_date = parse_date(form.get("due_date"))
        item.due_km = parse_int(form.get("due_km"))
        if item.due_date is None and item.due_km is None:
            raise ValueError("Indique une date ou un kilométrage")
        item.repeat_months = parse_int(form.get("repeat_months"))
        item.notes = clean(form.get("notes"))

    @app.post("/reminders/new")
    async def reminder_create(request: Request):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            item = Reminder(vehicle_id=vehicle.id)
            try:
                fill_reminder(item, form)
            except ValueError as exc:
                return reminders_page(request, session, vehicle, form=dict(form), error=str(exc))
            session.add(item)
            session.commit()
        return redirect(request, "/reminders")

    @app.get("/reminders/{item_id}", response_class=HTMLResponse)
    def reminder_edit(request: Request, item_id: int):
        with Sessions() as session:
            return render(request, "reminder_form.html", item=get_or_404(session, Reminder, item_id))

    @app.post("/reminders/{item_id}")
    async def reminder_update(request: Request, item_id: int):
        form = await request.form()
        with Sessions() as session:
            item = get_or_404(session, Reminder, item_id)
            try:
                fill_reminder(item, form)
            except ValueError as exc:
                session.rollback()
                return render(request, "reminder_form.html", item=item, form=dict(form), error=str(exc))
            item.done_date = parse_date(form.get("done_date")) if form.get("done_date") else item.done_date
            session.commit()
        return redirect(request, "/reminders")

    @app.post("/reminders/{item_id}/done")
    def reminder_done(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, Reminder, item_id)
            if item.repeat_months and item.due_date:
                # Rappel périodique : on passe à l'échéance suivante.
                item.due_date = add_months(item.due_date, item.repeat_months)
                while item.due_date < today():
                    item.due_date = add_months(item.due_date, item.repeat_months)
            else:
                item.done_date = today()
            session.commit()
        return redirect(request, "/reminders")

    @app.post("/reminders/{item_id}/reopen")
    def reminder_reopen(request: Request, item_id: int):
        with Sessions() as session:
            get_or_404(session, Reminder, item_id).done_date = None
            session.commit()
        return redirect(request, "/reminders")

    @app.post("/reminders/{item_id}/delete")
    def reminder_delete(request: Request, item_id: int):
        with Sessions() as session:
            session.delete(get_or_404(session, Reminder, item_id))
            session.commit()
        return redirect(request, "/reminders")

    @app.post("/inspections")
    async def inspection_add(request: Request):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            try:
                when = parse_date(form.get("date"))
                if when is None:
                    raise ValueError("La date de l'expertise est obligatoire")
                item = Inspection(
                    vehicle_id=vehicle.id,
                    date=when,
                    odometer_km=parse_int(form.get("odometer_km")),
                    notes=clean(form.get("notes")),
                )
                session.add(item)
                session.flush()
                item.expense_id = linked_expense(
                    session,
                    vehicle,
                    None,
                    parse_money(form.get("amount")),
                    "Expertise",
                    date=when,
                    vendor="Office de la circulation",
                    description="Expertise du véhicule",
                    odometer_km=item.odometer_km,
                )
            except ValueError as exc:
                session.rollback()
                return reminders_page(request, session, vehicle, form=dict(form), error=str(exc))
            session.commit()
        return redirect(request, "/reminders")

    @app.post("/inspections/{item_id}/delete")
    def inspection_delete(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, Inspection, item_id)
            if item.expense_id and (expense := session.get(Expense, item.expense_id)):
                session.delete(expense)
            session.delete(item)
            session.commit()
        return redirect(request, "/reminders")

    # ---------------------------------------------------------------- économies

    @app.get("/savings", response_class=HTMLResponse)
    def savings_page(request: Request):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            index = sync.load_fuel_index(session)
            rows = costs.savings(session, vehicle, index, today())
            settings = sync.get_settings(session)
            return render(
                request,
                "savings.html",
                vehicle=vehicle,
                rows=list(reversed(rows)),
                petrol=sum(r.petrol_cents for r in rows),
                electric=sum(r.electric_cents for r in rows),
                saved=sum(r.saved_cents for r in rows),
                litres=sum((r.km or 0) * vehicle.fuel_consumption_l100 / 100 for r in rows) if rows else 0,
                has_index=bool(index),
                status=settings,
            )
