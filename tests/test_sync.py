import sqlite3
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import sync
from app.charts import cost_and_km_chart
from app.costs import monthly, summarize
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import ChargingSession, OdometerReading, Vehicle

NOW = datetime(2026, 10, 23, 12, 0)

EVCC_SESSIONS = [
    {"id": 2, "created": "2026-10-20T18:00:00+02:00", "finished": "2026-10-20T22:00:00+02:00", "loadpoint": "Garage",
     "vehicle": "Citigo", "odometer": 14010.4, "chargedEnergy": 20.0, "solarPercentage": 25.0,
     "price": 4.1, "pricePerKWh": 0.205},
    {"id": 1, "created": "2026-09-10T10:00:00+02:00", "finished": "0001-01-01T00:00:00Z", "vehicle": "Autre",
     "chargedEnergy": 10.0, "price": 1.5, "pricePerKWh": 0.15},
    {"id": 3, "created": "2026-09-11T10:00:00+02:00", "chargedEnergy": 0.0, "price": 0},
]


@pytest.fixture
def db(tmp_path):
    session = make_sessionmaker(make_engine(tmp_path))()
    create_app(tmp_path, sync_interval=None)  # crée les tables
    vehicle = Vehicle(name="Citigo", odometer_entity="sensor.216980_odometer", evcc_url="http://evcc:7070")
    session.add(vehicle)
    session.commit()
    yield session, vehicle
    session.close()


def fake_fetch(responses):
    calls = []

    def fetch(url, token=None, timeout=20):
        calls.append(url)
        for prefix, payload in responses.items():
            if prefix in url:
                return payload
        raise OSError(f"injoignable : {url}")

    fetch.calls = calls
    return fetch


def test_evcc_sessions_are_imported_with_evcc_cost(db, monkeypatch):
    session, vehicle = db
    monkeypatch.setattr(sync, "fetch_json", fake_fetch({"/api/sessions": EVCC_SESSIONS}))
    sync.run_sync(session, NOW, ha=None)
    rows = session.query(ChargingSession).order_by(ChargingSession.evcc_id).all()
    assert [r.evcc_id for r in rows] == [1, 2]  # la session à 0 kWh est ignorée
    assert rows[1].cost_cents == 410 and rows[1].odometer_km == 14010 and rows[1].solar_pct == 25.0
    assert rows[0].finished is None  # session en cours
    assert sync.get_settings(session)["sync_evcc"].startswith("OK")

    # Réimporter met à jour sans doublon ; le filtre véhicule ne garde que la Citigo.
    vehicle.evcc_vehicle = "citigo"
    EVCC_SESSIONS[0]["price"] = 4.5
    monkeypatch.setattr(sync, "fetch_json", fake_fetch({"/api/sessions": {"result": EVCC_SESSIONS}}))
    sync.run_sync(session, NOW, ha=None)
    assert session.query(ChargingSession).count() == 2
    assert session.query(ChargingSession).filter_by(evcc_id=2).one().cost_cents == 450


def test_odometer_history_keeps_one_reading_per_day(db, monkeypatch):
    session, vehicle = db
    state = {"state": "14020.6", "last_changed": "2026-10-23T09:00:00+00:00",
             "attributes": {"unit_of_measurement": "km"}}
    history = [[
        {"state": "13900", "last_changed": "2026-10-21T08:00:00+00:00"},
        {"state": "13950", "last_changed": "2026-10-21T17:00:00+00:00"},
        {"state": "unavailable", "last_changed": "2026-10-22T08:00:00+00:00"},
    ]]
    fetch = fake_fetch({"/states/": state, "/history/period/": history})
    monkeypatch.setattr(sync, "fetch_json", fetch)
    sync.run_sync(session, NOW, ha=("http://supervisor/core/api", "token"))
    readings = {r.date: r.km for r in session.query(OdometerReading).filter_by(source="ha")}
    assert readings == {date(2026, 10, 21): 13950, date(2026, 10, 23): 14021}
    assert "filter_entity_id=sensor.216980_odometer" in fetch.calls[1]


def test_failures_are_reported_not_raised(db, monkeypatch):
    session, _ = db
    monkeypatch.setattr(sync, "fetch_json", fake_fetch({}))
    sync.run_sync(session, NOW, ha=("http://supervisor/core/api", "token"))
    status = sync.get_settings(session)
    assert status["sync_odometer"].startswith("Erreur") and status["sync_evcc"].startswith("Erreur")


def test_charging_cost_counts_in_summary_and_chart(db):
    session, vehicle = db
    vehicle.purchase_date = date(2026, 8, 1)
    vehicle.purchase_odometer_km = 13000
    vehicle.estimate_charging = False  # EVCC seul ; l'estimation a ses propres tests
    session.add(ChargingSession(vehicle_id=vehicle.id, evcc_id=9, started=datetime(2026, 9, 5, 20), energy_kwh=15,
                                cost_cents=300, odometer_km=13500))
    session.add(OdometerReading(vehicle_id=vehicle.id, date=date(2026, 10, 20), km=14000))
    session.commit()
    summary = summarize(session, vehicle, date(2026, 9, 1), date(2026, 9, 30))
    assert summary.charging_cents == 300 and summary.energy_kwh == 15
    assert ("Recharge à domicile (EVCC)", "variable", 300) in summary.by_category
    months = monthly(session, vehicle, date(2026, 10, 23))
    assert [m.start for m in months] == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    assert months[-1].per_km_cents == pytest.approx(300 / 1000)
    svg = cost_and_km_chart(months)
    assert svg.startswith("<svg") and "Km parcourus par mois" in svg


def test_existing_database_gets_new_columns(tmp_path):
    # Base de la version 0.1 : la table vehicle n'a pas encore evcc_url.
    con = sqlite3.connect(tmp_path / "car_costs.db")
    con.execute("CREATE TABLE vehicle (id INTEGER PRIMARY KEY, name VARCHAR(100), depreciation_years INTEGER, "
                "residual_value_cents INTEGER)")
    con.execute("INSERT INTO vehicle (name, depreciation_years, residual_value_cents) VALUES ('Citigo', 10, 0)")
    con.commit()
    con.close()
    with TestClient(create_app(tmp_path, sync_interval=None)) as client:
        assert client.get("/vehicle").status_code == 200
        assert client.get("/charging").status_code == 200


def test_vehicle_filter_matches_title_and_reports_ignored(db, monkeypatch):
    session, vehicle = db
    vehicle.evcc_vehicle = "citigo"
    sessions = [
        {"id": 10, "created": "2026-05-01T18:00:00+02:00", "vehicle": "Skoda Citigo e iV", "chargedEnergy": 12.0, "price": 2.0},
        {"id": 11, "created": "2026-05-02T18:00:00+02:00", "vehicle": "", "chargedEnergy": 8.0, "price": 1.0},
        {"id": 12, "created": "2026-05-03T18:00:00+02:00", "vehicle": "Tesla invité", "chargedEnergy": 30.0, "price": 6.0},
    ]
    monkeypatch.setattr(sync, "fetch_json", fake_fetch({"/api/sessions": sessions}))
    sync.run_sync(session, NOW, ha=None)
    assert sorted(r.evcc_id for r in session.query(ChargingSession)) == [10, 11]
    status = sync.get_settings(session)["sync_evcc"]
    assert "2 sessions importées sur 3 reçues" in status and "Tesla invité" in status


def test_unexpected_evcc_answer_is_an_error(db, monkeypatch):
    session, _ = db
    monkeypatch.setattr(sync, "fetch_json", fake_fetch({"/api/sessions": {"error": "database offline"}}))
    sync.run_sync(session, NOW, ha=None)
    assert "database offline" in sync.get_settings(session)["sync_evcc"]
