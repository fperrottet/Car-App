"""Modèle de données. Les montants sont stockés en centimes (entiers)."""

from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PERIODS = {"mensuel": 1, "trimestriel": 3, "semestriel": 6, "annuel": 12}
CONTRACT_KINDS = ["Assurance", "Garantie", "Abonnement de recharge", "Taxe / vignette", "Leasing / crédit", "Autre"]
CATEGORY_KINDS = ["fixe", "variable"]

DEFAULT_CATEGORIES = [
    ("Assurance", "fixe"),
    ("Taxe et vignette", "fixe"),
    ("Abonnement", "fixe"),
    ("Entretien", "variable"),
    ("Pneus", "variable"),
    ("Réparation", "variable"),
    ("Recharge à domicile", "variable"),
    ("Recharge externe", "variable"),
    ("Parking et péage", "variable"),
    ("Lavage", "variable"),
    ("Accessoires", "variable"),
    ("Autre", "variable"),
]


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    make: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    plate: Mapped[str | None] = mapped_column(String(20))
    vin: Mapped[str | None] = mapped_column(String(40))
    purchase_date: Mapped[date | None]
    purchase_price_cents: Mapped[int | None]
    purchase_odometer_km: Mapped[int | None]
    depreciation_years: Mapped[int] = mapped_column(default=10)
    residual_value_cents: Mapped[int] = mapped_column(default=0)
    # Préparé pour l'étape 2 (imports Home Assistant et EVCC).
    odometer_entity: Mapped[str | None] = mapped_column(String(200))
    evcc_vehicle: Mapped[str | None] = mapped_column(String(100))
    evcc_url: Mapped[str | None] = mapped_column(String(200))


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="variable")


class Contract(Base):
    """Contrat (assurance, garantie, taxe...) et, si un montant et une
    périodicité sont donnés, la charge fixe qu'il génère."""

    __tablename__ = "contract"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(200))
    policy_number: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date]
    end_date: Mapped[date | None]
    notice_days: Mapped[int | None]
    coverage: Mapped[str | None] = mapped_column(Text)
    deductible_cents: Mapped[int | None]
    amount_cents: Mapped[int | None]
    period: Mapped[str | None] = mapped_column(String(20))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)

    category: Mapped[Category | None] = relationship()

    @property
    def label(self) -> str:
        return f"{self.kind} {self.provider}"


class Maintenance(Base):
    __tablename__ = "maintenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    date: Mapped[date]
    odometer_km: Mapped[int | None]
    garage: Mapped[str | None] = mapped_column(String(200))
    work: Mapped[str] = mapped_column(Text)
    next_date: Mapped[date | None]
    next_km: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)


class Expense(Base):
    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    date: Mapped[date]
    amount_cents: Mapped[int]
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    vendor: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    odometer_km: Mapped[int | None]
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contract.id", ondelete="SET NULL"))
    maintenance_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance.id", ondelete="SET NULL"))
    # True pour les échéances créées automatiquement depuis un contrat.
    generated: Mapped[bool] = mapped_column(default=False)

    category: Mapped[Category | None] = relationship()
    contract: Mapped[Contract | None] = relationship()
    maintenance: Mapped[Maintenance | None] = relationship()


class OdometerReading(Base):
    __tablename__ = "odometer_reading"
    __table_args__ = (UniqueConstraint("vehicle_id", "date", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    date: Mapped[date]
    km: Mapped[int]
    source: Mapped[str] = mapped_column(String(20), default="manuel")


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(100), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    uploaded_at: Mapped[datetime]
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id", ondelete="SET NULL"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contract.id", ondelete="SET NULL"))
    maintenance_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance.id", ondelete="SET NULL"))

    expense: Mapped[Expense | None] = relationship()
    contract: Mapped[Contract | None] = relationship()
    maintenance: Mapped[Maintenance | None] = relationship()


class ChargingSession(Base):
    """Session de charge importée d'EVCC ; le coût est celui calculé par EVCC."""

    __tablename__ = "charging_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    evcc_id: Mapped[int] = mapped_column(unique=True)
    started: Mapped[datetime]
    finished: Mapped[datetime | None]
    loadpoint: Mapped[str | None] = mapped_column(String(100))
    evcc_vehicle: Mapped[str | None] = mapped_column(String(100))
    energy_kwh: Mapped[float]
    cost_cents: Mapped[int | None]
    price_per_kwh: Mapped[float | None]
    solar_pct: Mapped[float | None]
    odometer_km: Mapped[int | None]


class Setting(Base):
    """Petites valeurs persistantes (état de la dernière synchronisation)."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
