"""Application web : routes et rendu des pages."""

import asyncio
import csv
import io
import logging
from contextlib import asynccontextmanager
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from . import charts, costs, sync
from .db import Base, add_missing_columns, make_engine, make_sessionmaker
from .models import (
    CATEGORY_KINDS,
    CONTRACT_KINDS,
    DEFAULT_CATEGORIES,
    PERIODS,
    Category,
    ChargingSession,
    Contract,
    Document,
    Expense,
    Maintenance,
    OdometerReading,
    Vehicle,
)
from .util import clean, format_date, format_km, format_money, parse_date, parse_int, parse_money

HERE = Path(__file__).parent
log = logging.getLogger("car_costs")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["money"] = format_money
templates.env.filters["fdate"] = format_date
templates.env.filters["km"] = format_km
templates.env.filters["amount"] = lambda cents: "" if cents is None else f"{cents / 100:.2f}"
templates.env.filters["iso"] = lambda d: d.isoformat() if d else ""
templates.env.filters["options"] = lambda items, attr="name": [(i.id, getattr(i, attr)) for i in items]
templates.env.filters["zip_self"] = lambda items: [(i, i) for i in items]


def create_app(data_dir: Path | None = None, today=date.today, sync_interval: float | None = 3600) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("CAR_COSTS_DATA", "data"))
    docs_dir = data_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(data_dir)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    Sessions = make_sessionmaker(engine)

    with Sessions() as session:
        if not session.scalar(select(func.count(Category.id))):
            session.add_all(Category(name=n, kind=k) for n, k in DEFAULT_CATEGORIES)
            session.commit()

    def run_sync() -> None:
        with Sessions() as session:
            sync.run_sync(session)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def loop():
            while True:
                try:
                    await asyncio.to_thread(run_sync)
                except Exception:  # ne jamais arrêter la boucle
                    log.exception("Synchronisation en échec")
                await asyncio.sleep(sync_interval)

        task = asyncio.create_task(loop()) if sync_interval else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Frais de voiture", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def ingress_base(request: Request, call_next):
        # Derrière l'Ingress de Home Assistant, l'app est servie sous un
        # préfixe (/api/hassio_ingress/<jeton>) transmis dans cet en-tête.
        request.state.base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        return await call_next(request)

    def render(request: Request, name: str, **context) -> HTMLResponse:
        context.setdefault("form", {})
        context.setdefault("error", None)
        return templates.TemplateResponse(request, name, {"base": request.state.base, **context})

    def redirect(request: Request, path: str) -> RedirectResponse:
        return RedirectResponse(f"{request.state.base}{path}", status_code=303)

    def get_vehicle(session: Session) -> Vehicle | None:
        return session.scalars(select(Vehicle).order_by(Vehicle.id)).first()

    def categories(session: Session) -> list[Category]:
        return list(session.scalars(select(Category).order_by(Category.kind, Category.name)))

    def get_or_404(session: Session, model, item_id: int):
        item = session.get(model, item_id)
        if item is None:
            raise HTTPException(404)
        return item

    async def save_upload(session: Session, vehicle: Vehicle, upload, title: str | None, **links) -> None:
        if not isinstance(upload, UploadFile) or not upload.filename:
            return
        stored = f"{uuid.uuid4().hex}{Path(upload.filename).suffix.lower()[:10]}"
        size = 0
        with open(docs_dir / stored, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    (docs_dir / stored).unlink()
                    raise ValueError("Fichier trop volumineux (25 Mo maximum)")
                out.write(chunk)
        session.add(
            Document(
                vehicle_id=vehicle.id,
                title=title or upload.filename,
                original_name=upload.filename,
                stored_name=stored,
                content_type=upload.content_type or "application/octet-stream",
                size=size,
                uploaded_at=datetime.now(),
                **links,
            )
        )

    def documents_for(session: Session, **link) -> list[Document]:
        (column, value), = link.items()
        return list(session.scalars(select(Document).where(getattr(Document, column) == value)))

    # ---------------------------------------------------------------- tableau de bord

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, period: str = "year"):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            now = today()
            costs.sync_all_contracts(session, now)
            years = sorted(
                {int(y) for y in session.scalars(select(extract("year", Expense.date)).distinct())} | {now.year},
                reverse=True,
            )
            if period == "12m":
                start, label = now - timedelta(days=365), "12 derniers mois"
            elif period == "all":
                first = session.scalar(select(func.min(Expense.date)))
                start = min(d for d in (vehicle.purchase_date, first, now) if d)
                label = "Depuis l'achat"
            elif period.isdigit():
                start, label = date(int(period), 1, 1), period
            else:
                period, start, label = "year", date(now.year, 1, 1), "Cette année"
            end = date(start.year, 12, 31) if period.isdigit() and int(period) < now.year else now
            summary = costs.summarize(session, vehicle, start, end)
            return render(
                request,
                "dashboard.html",
                vehicle=vehicle,
                summary=summary,
                period=period,
                label=label,
                years=years,
                reminders=costs.reminders(session, vehicle, now),
                km_now=costs.current_km(session, vehicle),
                chart=charts.cost_and_km_chart(costs.monthly(session, vehicle, now)),
            )

    # ---------------------------------------------------------------- véhicule

    @app.get("/vehicle", response_class=HTMLResponse)
    def vehicle_page(request: Request):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            readings = []
            if vehicle:
                readings = list(
                    session.scalars(
                        select(OdometerReading)
                        .where(OdometerReading.vehicle_id == vehicle.id)
                        .order_by(OdometerReading.date.desc())
                    )
                )
            return render(
                request,
                "vehicle.html",
                vehicle=vehicle,
                readings=readings,
                today=today(),
                status=sync.get_settings(session),
            )

    @app.post("/vehicle")
    async def vehicle_save(request: Request):
        form = dict(await request.form())
        with Sessions() as session:
            vehicle = get_vehicle(session) or Vehicle()
            try:
                vehicle.name = clean(form.get("name")) or "Ma voiture"
                vehicle.make = clean(form.get("make"))
                vehicle.model = clean(form.get("model"))
                vehicle.plate = clean(form.get("plate"))
                vehicle.vin = clean(form.get("vin"))
                vehicle.purchase_date = parse_date(form.get("purchase_date"))
                vehicle.purchase_price_cents = parse_money(form.get("purchase_price"))
                vehicle.purchase_odometer_km = parse_int(form.get("purchase_odometer_km"))
                vehicle.depreciation_years = parse_int(form.get("depreciation_years")) or 10
                vehicle.residual_value_cents = parse_money(form.get("residual_value")) or 0
                vehicle.odometer_entity = clean(form.get("odometer_entity"))
                vehicle.evcc_vehicle = clean(form.get("evcc_vehicle"))
                vehicle.evcc_url = clean(form.get("evcc_url"))
                if vehicle.evcc_url and not vehicle.evcc_url.startswith(("http://", "https://")):
                    vehicle.evcc_url = "http://" + vehicle.evcc_url
            except ValueError as exc:
                return render(
                    request, "vehicle.html", vehicle=vehicle, readings=[], today=today(), status={}, form=form, error=str(exc)
                )
            session.add(vehicle)
            session.commit()
        return redirect(request, "/")

    @app.post("/vehicle/readings")
    async def reading_add(request: Request):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            try:
                when = parse_date(form.get("date")) or today()
                km = parse_int(form.get("km"))
            except ValueError:
                return redirect(request, "/vehicle")
            if km is not None:
                existing = session.scalars(
                    select(OdometerReading).where(
                        OdometerReading.vehicle_id == vehicle.id,
                        OdometerReading.date == when,
                        OdometerReading.source == "manuel",
                    )
                ).first()
                if existing:
                    existing.km = km
                else:
                    session.add(OdometerReading(vehicle_id=vehicle.id, date=when, km=km, source="manuel"))
                session.commit()
        return redirect(request, "/vehicle")

    @app.post("/vehicle/readings/{reading_id}/delete")
    def reading_delete(request: Request, reading_id: int):
        with Sessions() as session:
            session.delete(get_or_404(session, OdometerReading, reading_id))
            session.commit()
        return redirect(request, "/vehicle")

    # ---------------------------------------------------------------- synchronisation et recharges

    @app.post("/sync")
    async def sync_now(request: Request):
        form = await request.form()
        await asyncio.to_thread(run_sync)
        back = form.get("back") or "/charging"
        return redirect(request, back if back.startswith("/") and not back.startswith("//") else "/charging")

    @app.get("/charging", response_class=HTMLResponse)
    def charging_list(request: Request):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            items = list(
                session.scalars(
                    select(ChargingSession)
                    .where(ChargingSession.vehicle_id == vehicle.id)
                    .order_by(ChargingSession.started.desc())
                )
            )
            months: dict[date, dict] = {}
            for item in items:
                key = item.started.date().replace(day=1)
                month = months.setdefault(key, {"month": key, "count": 0, "kwh": 0.0, "cents": 0, "solar_kwh": 0.0, "items": []})
                month["count"] += 1
                month["kwh"] += item.energy_kwh
                month["cents"] += item.cost_cents or 0
                month["solar_kwh"] += item.energy_kwh * (item.solar_pct or 0) / 100
                month["items"].append(item)
            return render(
                request,
                "charging.html",
                vehicle=vehicle,
                months=list(months.values()),
                status=sync.get_settings(session),
            )

    # ---------------------------------------------------------------- dépenses

    @app.get("/expenses", response_class=HTMLResponse)
    def expense_list(request: Request, year: str = "", category: str = ""):
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            costs.sync_all_contracts(session, today())
            query = select(Expense).where(Expense.vehicle_id == vehicle.id)
            if year.isdigit():
                query = query.where(extract("year", Expense.date) == int(year))
            if category.isdigit():
                query = query.where(Expense.category_id == int(category))
            expenses = list(session.scalars(query.order_by(Expense.date.desc(), Expense.id.desc())))
            years = sorted({int(y) for y in session.scalars(select(extract("year", Expense.date)).distinct())}, reverse=True)
            doc_counts = dict(
                session.execute(
                    select(Document.expense_id, func.count(Document.id))
                    .where(Document.expense_id.is_not(None))
                    .group_by(Document.expense_id)
                ).all()
            )
            return render(
                request,
                "expenses.html",
                expenses=expenses,
                total=sum(e.amount_cents for e in expenses),
                years=years,
                year=year,
                category=category,
                categories=categories(session),
                doc_counts=doc_counts,
            )

    @app.get("/expenses.csv")
    def expense_csv(request: Request):
        with Sessions() as session:
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter=";")
            writer.writerow(["Date", "Montant CHF", "Catégorie", "Type", "Fournisseur", "Description", "Km", "Contrat", "Auto"])
            for e in session.scalars(select(Expense).order_by(Expense.date)):
                writer.writerow(
                    [
                        e.date.isoformat(),
                        f"{e.amount_cents / 100:.2f}",
                        e.category.name if e.category else "",
                        e.category.kind if e.category else "",
                        e.vendor or "",
                        e.description or "",
                        e.odometer_km or "",
                        f"{e.contract.kind} {e.contract.provider}" if e.contract else "",
                        "oui" if e.generated else "",
                    ]
                )
        return Response(
            "﻿" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="depenses.csv"'},
        )

    def expense_form(request: Request, session: Session, expense: Expense | None, form=None, error=None):
        return render(
            request,
            "expense_form.html",
            expense=expense,
            categories=categories(session),
            contracts=list(session.scalars(select(Contract).order_by(Contract.start_date.desc()))),
            documents=documents_for(session, expense_id=expense.id) if expense and expense.id else [],
            today=today(),
            form=form or {},
            error=error,
        )

    async def expense_save(request: Request, expense: Expense | None):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            if expense is not None:
                expense = session.merge(expense)
            else:
                expense = Expense(vehicle_id=vehicle.id)
            try:
                expense.date = parse_date(form.get("date")) or today()
                amount = parse_money(form.get("amount"))
                if amount is None:
                    raise ValueError("Le montant est obligatoire")
                expense.amount_cents = amount
                expense.category_id = parse_int(form.get("category_id"))
                expense.vendor = clean(form.get("vendor"))
                expense.description = clean(form.get("description"))
                expense.odometer_km = parse_int(form.get("odometer_km"))
                expense.contract_id = parse_int(form.get("contract_id"))
                session.add(expense)
                session.flush()
                await save_upload(session, vehicle, form.get("file"), clean(form.get("doc_title")), expense_id=expense.id)
            except ValueError as exc:
                session.rollback()
                return expense_form(request, session, expense if expense.id else None, dict(form), str(exc))
            session.commit()
        return redirect(request, "/expenses")

    @app.get("/expenses/new", response_class=HTMLResponse)
    def expense_new(request: Request):
        with Sessions() as session:
            return expense_form(request, session, None)

    @app.post("/expenses/new")
    async def expense_create(request: Request):
        return await expense_save(request, None)

    @app.get("/expenses/{expense_id}", response_class=HTMLResponse)
    def expense_edit(request: Request, expense_id: int):
        with Sessions() as session:
            return expense_form(request, session, get_or_404(session, Expense, expense_id))

    @app.post("/expenses/{expense_id}")
    async def expense_update(request: Request, expense_id: int):
        with Sessions() as session:
            expense = get_or_404(session, Expense, expense_id)
        return await expense_save(request, expense)

    @app.post("/expenses/{expense_id}/delete")
    def expense_delete(request: Request, expense_id: int):
        with Sessions() as session:
            expense = get_or_404(session, Expense, expense_id)
            if expense.generated:
                # Une échéance supprimée serait recréée au prochain calcul :
                # on la garde à zéro pour marquer qu'elle a été annulée.
                expense.amount_cents = 0
                expense.description = (expense.description or "") + " (annulée)"
            else:
                session.delete(expense)
            session.commit()
        return redirect(request, "/expenses")

    # ---------------------------------------------------------------- contrats

    @app.get("/contracts", response_class=HTMLResponse)
    def contract_list(request: Request):
        with Sessions() as session:
            if get_vehicle(session) is None:
                return redirect(request, "/vehicle")
            contracts = list(session.scalars(select(Contract).order_by(Contract.end_date.is_(None), Contract.start_date.desc())))
            return render(request, "contracts.html", contracts=contracts, today=today(), periods=PERIODS)

    def contract_form(request: Request, session: Session, contract: Contract | None, form=None, error=None):
        return render(
            request,
            "contract_form.html",
            contract=contract,
            kinds=CONTRACT_KINDS,
            periods=list(PERIODS),
            categories=categories(session),
            documents=documents_for(session, contract_id=contract.id) if contract and contract.id else [],
            form=form or {},
            error=error,
        )

    async def contract_save(request: Request, contract: Contract | None):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            contract = session.merge(contract) if contract is not None else Contract(vehicle_id=vehicle.id)
            try:
                contract.kind = clean(form.get("kind")) or "Autre"
                contract.provider = clean(form.get("provider")) or ""
                if not contract.provider:
                    raise ValueError("Le fournisseur est obligatoire")
                contract.policy_number = clean(form.get("policy_number"))
                start = parse_date(form.get("start_date"))
                if start is None:
                    raise ValueError("La date de début est obligatoire")
                contract.start_date = start
                contract.end_date = parse_date(form.get("end_date"))
                if contract.end_date and contract.end_date <= start:
                    raise ValueError("La date de fin doit suivre la date de début")
                contract.notice_days = parse_int(form.get("notice_days"))
                contract.coverage = clean(form.get("coverage"))
                contract.deductible_cents = parse_money(form.get("deductible"))
                contract.amount_cents = parse_money(form.get("amount"))
                contract.period = clean(form.get("period")) if contract.amount_cents else None
                if contract.period and contract.period not in PERIODS:
                    raise ValueError("Périodicité inconnue")
                contract.category_id = parse_int(form.get("category_id"))
                contract.notes = clean(form.get("notes"))
                session.add(contract)
                session.flush()
                # Les échéances automatiques sont recalculées à partir du contrat.
                for old in session.scalars(
                    select(Expense).where(Expense.contract_id == contract.id, Expense.generated.is_(True))
                ):
                    session.delete(old)
                session.flush()
                costs.sync_contract_expenses(session, contract, today())
                await save_upload(session, vehicle, form.get("file"), clean(form.get("doc_title")), contract_id=contract.id)
            except ValueError as exc:
                session.rollback()
                return contract_form(request, session, contract if contract.id else None, dict(form), str(exc))
            session.commit()
        return redirect(request, "/contracts")

    @app.get("/contracts/new", response_class=HTMLResponse)
    def contract_new(request: Request):
        with Sessions() as session:
            return contract_form(request, session, None)

    @app.post("/contracts/new")
    async def contract_create(request: Request):
        return await contract_save(request, None)

    @app.get("/contracts/{contract_id}", response_class=HTMLResponse)
    def contract_edit(request: Request, contract_id: int):
        with Sessions() as session:
            return contract_form(request, session, get_or_404(session, Contract, contract_id))

    @app.post("/contracts/{contract_id}")
    async def contract_update(request: Request, contract_id: int):
        with Sessions() as session:
            contract = get_or_404(session, Contract, contract_id)
        return await contract_save(request, contract)

    @app.post("/contracts/{contract_id}/delete")
    def contract_delete(request: Request, contract_id: int):
        with Sessions() as session:
            contract = get_or_404(session, Contract, contract_id)
            for old in session.scalars(select(Expense).where(Expense.contract_id == contract.id, Expense.generated.is_(True))):
                session.delete(old)
            session.delete(contract)
            session.commit()
        return redirect(request, "/contracts")

    # ---------------------------------------------------------------- entretien

    @app.get("/maintenance", response_class=HTMLResponse)
    def maintenance_list(request: Request):
        with Sessions() as session:
            if get_vehicle(session) is None:
                return redirect(request, "/vehicle")
            items = list(session.scalars(select(Maintenance).order_by(Maintenance.date.desc())))
            spent = dict(
                session.execute(
                    select(Expense.maintenance_id, func.sum(Expense.amount_cents))
                    .where(Expense.maintenance_id.is_not(None))
                    .group_by(Expense.maintenance_id)
                ).all()
            )
            return render(request, "maintenance.html", items=items, spent=spent)

    def maintenance_form(request: Request, session: Session, item: Maintenance | None, form=None, error=None):
        expense = None
        if item and item.id:
            expense = session.scalars(select(Expense).where(Expense.maintenance_id == item.id)).first()
        return render(
            request,
            "maintenance_form.html",
            item=item,
            expense=expense,
            documents=documents_for(session, maintenance_id=item.id) if item and item.id else [],
            today=today(),
            form=form or {},
            error=error,
        )

    async def maintenance_save(request: Request, item: Maintenance | None):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            item = session.merge(item) if item is not None else Maintenance(vehicle_id=vehicle.id)
            try:
                item.date = parse_date(form.get("date")) or today()
                item.odometer_km = parse_int(form.get("odometer_km"))
                item.garage = clean(form.get("garage"))
                item.work = clean(form.get("work")) or ""
                if not item.work:
                    raise ValueError("Décris les travaux effectués")
                item.next_date = parse_date(form.get("next_date"))
                item.next_km = parse_int(form.get("next_km"))
                item.notes = clean(form.get("notes"))
                amount = parse_money(form.get("amount"))
                session.add(item)
                session.flush()
                # Le coût de l'entretien est une dépense liée, catégorie Entretien.
                expense = session.scalars(select(Expense).where(Expense.maintenance_id == item.id)).first()
                if amount is not None:
                    if expense is None:
                        category = session.scalars(select(Category).where(Category.name == "Entretien")).first()
                        expense = Expense(
                            vehicle_id=vehicle.id,
                            maintenance_id=item.id,
                            category_id=category.id if category else None,
                        )
                        session.add(expense)
                    expense.date = item.date
                    expense.amount_cents = amount
                    expense.vendor = item.garage
                    expense.description = item.work.splitlines()[0][:200]
                    expense.odometer_km = item.odometer_km
                elif expense is not None:
                    session.delete(expense)
                await save_upload(session, vehicle, form.get("file"), clean(form.get("doc_title")), maintenance_id=item.id)
            except ValueError as exc:
                session.rollback()
                return maintenance_form(request, session, item if item.id else None, dict(form), str(exc))
            session.commit()
        return redirect(request, "/maintenance")

    @app.get("/maintenance/new", response_class=HTMLResponse)
    def maintenance_new(request: Request):
        with Sessions() as session:
            return maintenance_form(request, session, None)

    @app.post("/maintenance/new")
    async def maintenance_create(request: Request):
        return await maintenance_save(request, None)

    @app.get("/maintenance/{item_id}", response_class=HTMLResponse)
    def maintenance_edit(request: Request, item_id: int):
        with Sessions() as session:
            return maintenance_form(request, session, get_or_404(session, Maintenance, item_id))

    @app.post("/maintenance/{item_id}")
    async def maintenance_update(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, Maintenance, item_id)
        return await maintenance_save(request, item)

    @app.post("/maintenance/{item_id}/delete")
    def maintenance_delete(request: Request, item_id: int):
        with Sessions() as session:
            item = get_or_404(session, Maintenance, item_id)
            for expense in session.scalars(select(Expense).where(Expense.maintenance_id == item.id)):
                session.delete(expense)
            session.delete(item)
            session.commit()
        return redirect(request, "/maintenance")

    # ---------------------------------------------------------------- documents

    @app.get("/documents", response_class=HTMLResponse)
    def document_list(request: Request, q: str = ""):
        with Sessions() as session:
            if get_vehicle(session) is None:
                return redirect(request, "/vehicle")
            query = select(Document).order_by(Document.uploaded_at.desc())
            if q.strip():
                like = f"%{q.strip()}%"
                query = query.where(Document.title.ilike(like) | Document.original_name.ilike(like))
            return render(request, "documents.html", documents=list(session.scalars(query)), q=q)

    @app.post("/documents")
    async def document_upload(request: Request):
        form = await request.form()
        with Sessions() as session:
            vehicle = get_vehicle(session)
            if vehicle is None:
                return redirect(request, "/vehicle")
            try:
                await save_upload(session, vehicle, form.get("file"), clean(form.get("doc_title")))
            except ValueError as exc:
                return render(request, "documents.html", documents=[], q="", error=str(exc))
            session.commit()
        return redirect(request, "/documents")

    @app.get("/documents/{doc_id}/file")
    def document_file(doc_id: int, download: bool = False):
        with Sessions() as session:
            doc = get_or_404(session, Document, doc_id)
            path = docs_dir / doc.stored_name
            if not path.is_file():
                raise HTTPException(404)
            return FileResponse(
                path,
                media_type=doc.content_type,
                filename=doc.original_name,
                content_disposition_type="attachment" if download else "inline",
            )

    @app.post("/documents/{doc_id}/delete")
    async def document_delete(request: Request, doc_id: int):
        form = await request.form()
        with Sessions() as session:
            doc = get_or_404(session, Document, doc_id)
            (docs_dir / doc.stored_name).unlink(missing_ok=True)
            session.delete(doc)
            session.commit()
        back = form.get("back") or "/documents"
        return redirect(request, back if back.startswith("/") and not back.startswith("//") else "/documents")

    # ---------------------------------------------------------------- catégories

    @app.get("/categories", response_class=HTMLResponse)
    def category_list(request: Request):
        with Sessions() as session:
            used = dict(
                session.execute(select(Expense.category_id, func.count(Expense.id)).group_by(Expense.category_id)).all()
            )
            return render(request, "categories.html", categories=categories(session), kinds=CATEGORY_KINDS, used=used)

    @app.post("/categories")
    async def category_save(request: Request):
        form = await request.form()
        name = clean(form.get("name"))
        kind = form.get("kind") if form.get("kind") in CATEGORY_KINDS else "variable"
        with Sessions() as session:
            category_id = parse_int(form.get("id")) if form.get("id") else None
            category = session.get(Category, category_id) if category_id else Category()
            if name and category is not None:
                if session.scalars(select(Category).where(Category.name == name, Category.id != category_id)).first():
                    return redirect(request, "/categories")
                category.name, category.kind = name, kind
                session.add(category)
                session.commit()
        return redirect(request, "/categories")

    @app.post("/categories/{category_id}/delete")
    def category_delete(request: Request, category_id: int):
        with Sessions() as session:
            session.delete(get_or_404(session, Category, category_id))
            session.commit()
        return redirect(request, "/categories")

    return app
