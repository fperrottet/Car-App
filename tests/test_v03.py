import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import costs, sync
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import (
    BatterySnapshot,
    Category,
    ChargingSession,
    Expense,
    Inspection,
    OdometerReading,
    Reminder,
    TireMount,
    TireSet,
    Vehicle,
)

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 12, 0)


@pytest.fixture
def db(tmp_path):
    create_app(tmp_path, sync_interval=None)  # crée les tables et les catégories
    session = make_sessionmaker(make_engine(tmp_path))()
    vehicle = Vehicle(name="Citigo", purchase_date=date(2026, 4, 1), purchase_odometer_km=10000)
    session.add(vehicle)
    session.commit()
    yield session, vehicle
    session.close()


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, today=lambda: TODAY, sync_interval=None)
    with TestClient(app) as client:
        client.post("/vehicle", data={"name": "Citigo", "purchase_date": "2026-04-01", "purchase_odometer_km": "10000",
                                      "first_registration_date": "2021-06-15", "inspection_intervals": "5, 3, 2"})
        yield client


# ---------------------------------------------------------------- expertise


def test_inspection_schedule_follows_fribourg_rule(db):
    session, vehicle = db
    vehicle.first_registration_date = date(2021, 6, 15)
    assert costs.next_inspection(session, vehicle) == date(2026, 6, 15)  # 5 ans
    session.add(Inspection(vehicle_id=vehicle.id, date=date(2026, 5, 20)))
    session.flush()
    assert costs.next_inspection(session, vehicle) == date(2029, 5, 20)  # puis 3 ans
    session.add(Inspection(vehicle_id=vehicle.id, date=date(2029, 4, 2)))
    session.flush()
    assert costs.next_inspection(session, vehicle) == date(2031, 4, 2)  # puis tous les 2 ans


def test_inspection_rank_is_guessed_from_age_when_earlier_ones_are_missing(db):
    session, vehicle = db
    vehicle.first_registration_date = date(2015, 3, 1)
    session.add(Inspection(vehicle_id=vehicle.id, date=date(2023, 2, 10)))  # la 2e, la 1re n'est pas saisie
    session.flush()
    assert costs.next_inspection(session, vehicle) == date(2025, 2, 10)


def test_inspection_due_soon_is_on_the_dashboard(client):
    page = client.get("/").text
    # 5 ans après la 1re immatriculation, aucune expertise saisie : en retard.
    assert '<li class="overdue"><span>15.06.2026</span> Expertise à prévoir' in page


# ---------------------------------------------------------------- pneus


def test_tire_km_sum_mount_periods(db):
    session, vehicle = db
    session.add(OdometerReading(vehicle_id=vehicle.id, date=date(2026, 10, 1), km=16000))
    tires = TireSet(vehicle_id=vehicle.id, season="été", count=4, position="les 4")
    tires.mounts = [
        TireMount(mounted_date=date(2026, 4, 1), mounted_km=10000, removed_date=date(2026, 6, 1), removed_km=12000),
        TireMount(mounted_date=date(2026, 8, 1), mounted_km=14000),
    ]
    session.add(tires)
    session.flush()
    points = costs.odometer_points(session, vehicle)
    assert costs.tire_km(tires, points, costs.current_km(session, vehicle)) == 2000 + 2000


def test_mounting_two_front_tires_replaces_only_the_front(client):
    client.post("/tires/new", data={"season": "été", "count": "4", "size": "165/70 R14", "amount": "480",
                                    "mount_now": "1", "mount_date": "2026-04-01", "mount_km": "10000"})
    client.post("/tires/new", data={"season": "été", "count": "2", "position": "avant", "size": "165/70 R14",
                                    "mount_now": "1", "mount_date": "2026-09-01", "mount_km": "15000"})
    page = client.get("/tires").text
    assert page.count("165/70 R14") >= 2
    client.post("/tires/new", data={"season": "hiver", "count": "4", "mount_now": "1",
                                    "mount_date": "2026-10-20", "mount_km": "16000"})
    page = client.get("/tires").text
    stored = page.split("En stock")[1]
    assert stored.count("<tr>") - stored.count("<thead><tr>") == 2  # les deux jeux d'été sont démontés
    expense = client.get("/expenses").text
    assert "480.00" in expense and "Pneus" in expense


def test_low_tread_and_season_reminders(db):
    session, vehicle = db
    summer = TireSet(vehicle_id=vehicle.id, season="été")
    summer.mounts = [TireMount(mounted_date=date(2026, 4, 1))]
    winter = TireSet(vehicle_id=vehicle.id, season="hiver", brand="Nokian")
    winter.mounts = [TireMount(mounted_date=date(2025, 11, 1), removed_date=date(2026, 4, 1), tread_mm=3.5)]
    session.add_all([summer, winter])
    session.flush()
    texts = [a.text for a in costs.reminders(session, vehicle, TODAY)]
    assert any("penser aux pneus d'hiver" in t for t in texts)
    assert any("3.5 mm" in t for t in texts)


# ---------------------------------------------------------------- essence

FUEL_CSV = "Datum,Preis_LIK_indexiert\n2026-04-01 00:00:00,110\n2026-05-01 00:00:00,121\n"


def test_fuel_index_csv_is_parsed():
    assert sync.parse_fuel_index(FUEL_CSV) == {"2026-04": 110.0, "2026-05": 121.0}
    assert sync.parse_fuel_index("Datum;Preis\n2026-04-01;99,5\n") == {"2026-04": 99.5}


def test_savings_scale_the_reference_price_with_the_index(db):
    session, vehicle = db
    vehicle.fuel_consumption_l100 = 5.0
    vehicle.fuel_price_ref = 1.80
    vehicle.fuel_price_ref_date = date(2026, 5, 10)
    session.add_all([
        OdometerReading(vehicle_id=vehicle.id, date=date(2026, 4, 30), km=11000),
        OdometerReading(vehicle_id=vehicle.id, date=date(2026, 5, 31), km=12000),
        ChargingSession(vehicle_id=vehicle.id, evcc_id=1, started=datetime(2026, 5, 3, 20), energy_kwh=30, cost_cents=600),
    ])
    external = session.query(Category).filter_by(name="Recharge externe").one()
    session.add(Expense(vehicle_id=vehicle.id, date=date(2026, 5, 20), amount_cents=1500, category_id=external.id))
    session.flush()
    rows = costs.savings(session, vehicle, {"2026-04": 110.0, "2026-05": 121.0}, date(2026, 5, 31))
    april, may = rows
    assert april.price == pytest.approx(1.80 * 110 / 121)
    assert may.petrol_cents == round(1000 * 5 / 100 * 1.80 * 100)  # 50 l à 1.80
    assert may.electric_cents == 2100 and may.saved_cents == 9000 - 2100


def test_fuel_index_is_downloaded_once_a_day(db, monkeypatch):
    session, vehicle = db
    vehicle.fuel_consumption_l100, vehicle.fuel_price_ref = 5.5, 1.8
    calls = []
    monkeypatch.setattr(sync, "fetch_text", lambda url, *a, **k: calls.append(url) or FUEL_CSV)
    sync.run_sync(session, NOW, ha=None)
    sync.run_sync(session, NOW, ha=None)
    assert calls == [sync.FUEL_INDEX_URL]
    assert sync.load_fuel_index(session)["2026-05"] == 121.0
    assert sync.get_settings(session)["sync_fuel"].startswith("OK")


# ---------------------------------------------------------------- batterie

HA_STATES = [
    {"entity_id": "sensor.216980_odometer", "state": "12000", "attributes": {"unit_of_measurement": "km"}},
    {"entity_id": "sensor.parking_216980_tire_health", "state": "ok", "attributes": {}},
    {"entity_id": "sensor.ovms_citigo_xvu_b_soh_charge", "state": "93.4", "attributes": {"unit_of_measurement": "%"}},
    {"entity_id": "sensor.ovms_citigo_xvu_b_c_soh", "state": "unknown",
     "attributes": {"friendly_name": "Cell SoH", "values": [94.1, 93.0, 95.2, 92.4]}},
    {"entity_id": "sensor.ovms_citigo_xvu_b_hist_soh_mod_01", "state": "99,97.5,95.1", "attributes": {}},
]


def ha_fetch(url, token=None, timeout=20):
    if url.endswith("/states"):
        return HA_STATES
    raise OSError(url)


def test_battery_entities_are_discovered_and_stored(db, monkeypatch, no_network):
    session, vehicle = db
    monkeypatch.setattr(sync, "fetch_json", ha_fetch)
    sync.run_sync(session, NOW, ha=("http://supervisor/core/api", "token"))
    snap = session.query(BatterySnapshot).one()
    assert snap.soh == 93.4 and json.loads(snap.cells) == [94.1, 93.0, 95.2, 92.4]
    settings = sync.get_settings(session)
    assert "4 cellules (min 92.4 %)" in settings["sync_battery"]
    assert json.loads(settings["battery_modules"]) == {"sensor.ovms_citigo_xvu_b_hist_soh_mod_01": [99.0, 97.5, 95.1]}
    sync.run_sync(session, NOW, ha=("http://supervisor/core/api", "token"))
    assert session.query(BatterySnapshot).count() == 1  # un relevé par jour

    sent = {url.rsplit("/", 1)[1]: payload for url, payload in no_network}
    assert sent["sensor.frais_voiture_cellule_min"]["state"] == 92.4
    assert sent["sensor.frais_voiture_mois"]["attributes"]["unit_of_measurement"] == "CHF"


def test_entity_override_wins(db):
    _, vehicle = db
    vehicle.soh_entity = "sensor.my_soh"
    found = sync.discover_battery({e["entity_id"]: e for e in HA_STATES}, vehicle)
    assert found["soh"] == "sensor.my_soh" and found["cells"] == "sensor.ovms_citigo_xvu_b_c_soh"


def test_battery_page_renders(client, tmp_path):
    session = make_sessionmaker(make_engine(tmp_path))()
    session.add_all([
        BatterySnapshot(vehicle_id=1, date=date(2026, 6, 1), soh=95.0, cells=json.dumps([95.0, 94.0, 96.0])),
        BatterySnapshot(vehicle_id=1, date=date(2026, 10, 1), soh=94.2, cells=json.dumps([94.5, 93.1, 95.0])),
    ])
    session.commit()
    page = client.get("/battery").text
    assert "94.2 %" in page and "cellule 2" in page and "<svg" in page


# ---------------------------------------------------------------- rappels


def test_repeating_reminder_moves_to_next_due_date(client):
    client.post("/reminders/new", data={"title": "Vignette autoroutière", "due_date": "2026-01-31", "repeat_months": "12"})
    assert "Vignette autoroutière" in client.get("/").text  # dépassée
    client.post("/reminders/1/done")
    page = client.get("/reminders").text
    assert "31.01.2027" in page
    client.post("/reminders/new", data={"title": "Garantie", "due_date": "2026-11-30"})
    client.post("/reminders/2/done")
    assert "fait le 23.10.2026" in client.get("/reminders").text


def test_reminder_needs_a_date_or_km(client):
    page = client.post("/reminders/new", data={"title": "Rien"}).text
    assert "Indique une date ou un kilométrage" in page


def test_presets_and_inspection_with_cost(client):
    page = client.get("/reminders?preset=batterie").text
    assert "Fin de la garantie batterie" in page and "2029-06-15" in page and "160000" in page
    client.post("/inspections", data={"date": "2026-06-10", "odometer_km": "12000", "amount": "70"})
    page = client.get("/reminders").text
    assert "10.06.2029" in page  # 3 ans après la 1re expertise
    assert "Expertise" in client.get("/expenses").text


def test_new_pages_render(client):
    for path in ("/tires", "/tires/new", "/battery", "/reminders", "/savings", "/vehicle"):
        assert client.get(path).status_code == 200, path


def test_added_category_appears_in_existing_database(tmp_path):
    create_app(tmp_path, sync_interval=None)
    session = make_sessionmaker(make_engine(tmp_path))()
    session.query(Category).filter_by(name="Expertise").delete()
    session.commit()
    create_app(tmp_path, sync_interval=None)
    assert session.query(Category).filter_by(name="Expertise").count() == 1


def test_tread_can_be_noted_after_automatic_unmount(client):
    client.post("/tires/new", data={"season": "hiver", "count": "4", "brand": "Michelin",
                                    "mount_now": "1", "mount_date": "2025-11-05", "mount_km": "18000"})
    client.post("/tires/new", data={"season": "été", "count": "4", "mount_now": "1",
                                    "mount_date": "2026-04-02", "mount_km": "21000"})
    client.post("/tires/1/tread", data={"tread_mm": "3,6"})
    assert "3.6 mm mesurés" in client.get("/").text
