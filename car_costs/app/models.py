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
    ("Expertise", "variable"),
]
# Catégories apparues après la 0.1, ajoutées aux bases existantes si absentes.
ADDED_CATEGORIES = [("Expertise", "variable")]

TIRE_SEASONS = ["été", "hiver", "4 saisons"]
TIRE_POSITIONS = ["les 4", "avant", "arrière"]


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
    first_registration_date: Mapped[date | None]
    # Années entre deux expertises ; la dernière valeur se répète (Fribourg : 5, 3, puis 2).
    inspection_intervals: Mapped[str | None] = mapped_column(String(50))
    # Comparaison avec une voiture à essence équivalente.
    fuel_consumption_l100: Mapped[float | None]
    fuel_price_ref: Mapped[float | None]
    fuel_price_ref_date: Mapped[date | None]
    # Entités HA de la santé batterie (vide : recherche automatique des métriques OVMS).
    soh_entity: Mapped[str | None] = mapped_column(String(200))
    cell_soh_entity: Mapped[str | None] = mapped_column(String(200))
    # Tout le reste (n° matricule, prix catalogue, options...) en texte libre.
    notes: Mapped[str | None] = mapped_column(Text)


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
    coverages: Mapped[list["ContractCoverage"]] = relationship(
        cascade="all, delete-orphan", order_by="ContractCoverage.position"
    )

    @property
    def label(self) -> str:
        return f"{self.kind} {self.provider}"


# Couvertures usuelles d'une assurance auto suisse (suggestions du formulaire).
COVERAGE_NAMES = [
    "Responsabilité civile (RC)",
    "Casco partielle",
    "Casco complète (collision)",
    "Accidents des occupants",
    "Assistance et dépannage",
    "Faute grave",
    "Protection du bonus",
    "Dommages de parking",
    "Effets personnels",
    "Batterie et câble de recharge",
]


class ContractCoverage(Base):
    """Une ligne de la police : RC, casco partielle, casco complète, option...
    Une ligne non retenue garde le prix proposé pour comparer."""

    __tablename__ = "contract_coverage"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contract.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(200))
    included: Mapped[bool] = mapped_column(default=True)
    premium_cents: Mapped[int | None]
    deductible_cents: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)


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


class TireSet(Base):
    """Un achat de pneus (souvent 2 par 2), suivi sur ses périodes de montage."""

    __tablename__ = "tire_set"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    season: Mapped[str] = mapped_column(String(20))
    brand: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[str | None] = mapped_column(String(50))
    load_speed: Mapped[str | None] = mapped_column(String(20))
    dot: Mapped[str | None] = mapped_column(String(50))
    count: Mapped[int] = mapped_column(default=4)
    position: Mapped[str] = mapped_column(String(20), default="les 4")
    purchase_date: Mapped[date | None]
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id", ondelete="SET NULL"))
    retired_date: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(Text)

    mounts: Mapped[list["TireMount"]] = relationship(
        back_populates="tire_set", cascade="all, delete-orphan", order_by="TireMount.mounted_date"
    )
    expense: Mapped[Expense | None] = relationship()

    @property
    def label(self) -> str:
        parts = [self.brand, self.model, self.size]
        return " ".join(p for p in parts if p) or f"Pneus {self.season}"

    @property
    def current_mount(self) -> "TireMount | None":
        return next((m for m in self.mounts if m.removed_date is None), None)


class TireMount(Base):
    __tablename__ = "tire_mount"

    id: Mapped[int] = mapped_column(primary_key=True)
    tire_set_id: Mapped[int] = mapped_column(ForeignKey("tire_set.id", ondelete="CASCADE"))
    mounted_date: Mapped[date]
    mounted_km: Mapped[int | None]
    removed_date: Mapped[date | None]
    removed_km: Mapped[int | None]
    tread_mm: Mapped[float | None]

    tire_set: Mapped[TireSet] = relationship(back_populates="mounts")


class Inspection(Base):
    """Expertise (contrôle technique) passée."""

    __tablename__ = "inspection"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    date: Mapped[date]
    odometer_km: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id", ondelete="SET NULL"))


class Reminder(Base):
    """Rappel administratif (vignette, garantie, taxe...), éventuellement périodique."""

    __tablename__ = "reminder"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date | None]
    due_km: Mapped[int | None]
    repeat_months: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)
    done_date: Mapped[date | None]


class BatterySnapshot(Base):
    """Relevé quotidien de la santé de la batterie (OVMS via HA)."""

    __tablename__ = "battery_snapshot"
    __table_args__ = (UniqueConstraint("vehicle_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"))
    date: Mapped[date]
    soh: Mapped[float | None]
    cells: Mapped[str | None] = mapped_column(Text)  # JSON : SoH de chaque cellule
