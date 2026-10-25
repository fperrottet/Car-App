from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import costs, sync
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import Expense, Vehicle

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 12, 0)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, today=lambda: TODAY, sync_interval=None)
    with TestClient(app) as client:
        client.post("/vehicle", data={"name": "Citigo", "purchase_date": "2026-04-01", "purchase_odometer_km": "10000",
                                      "odometer_entity": "sensor.216980_odometer"})
        client.tmp_path = tmp_path
        yield client


def add_golf(client):
    return client.post("/vehicle", data={"id": "new", "name": "Golf", "energy": "essence", "connected": "0",
                                         "purchase_date": "2026-01-01", "purchase_odometer_km": "80000"})


def test_second_car_is_added_and_becomes_active(client):
    add_golf(client)
    page = client.get("/").text
    assert "<h1>Golf</h1>" in page
    assert "/fuel" in page and "/charging" not in page and "/battery" not in page  # menu d'une voiture thermique
    assert 'name="vehicle"' in page  # sélecteur, dès deux voitures
    listing = client.get("/vehicles").text
    assert "Citigo" in listing and "essence" in listing and "non connectée" in listing


def test_data_stays_with_its_car(client):
    client.post("/expenses/new", data={"date": "2026-05-01", "amount": "50", "description": "lavage citigo"})
    add_golf(client)
    client.post("/expenses/new", data={"date": "2026-05-02", "amount": "70", "description": "vidange golf"})
    client.post("/contracts/new", data={"kind": "Assurance", "provider": "AXA golf", "start_date": "2026-01-01"})
    golf = client.get("/expenses").text
    assert "vidange golf" in golf and "lavage citigo" not in golf
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        citigo = session.query(Vehicle).filter_by(name="Citigo").one()
    client.post("/vehicles/select", data={"vehicle": citigo.id, "back": "/expenses"})
    page = client.get("/expenses").text
    assert "lavage citigo" in page and "vidange golf" not in page
    assert "AXA golf" not in client.get("/contracts").text
    assert "vidange golf" not in client.get("/expenses.csv").text


def test_fuel_fills_give_consumption_and_count_as_expenses(client):
    add_golf(client)
    for day, km, liters, amount in (("2026-03-01", "80500", "40", "72"), ("2026-04-01", "81100", "36", "66.6"),
                                    ("2026-05-01", "81700", "30", "57")):
        client.post("/fuel", data={"date": day, "km": km, "liters": liters, "amount": amount, "vendor": "Migrol"})
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        golf = session.query(Vehicle).filter_by(name="Golf").one()
        fills = costs.fuel_log(session, golf)
        assert [f.liters_per_100km for f in fills] == [5.0, 6.0, None]  # le plus récent d'abord
        assert costs.fuel_average(fills) == 5.5
        assert all(f.expense.category.name == "Carburant" for f in fills)
        year = costs.summarize(session, golf, date(2026, 1, 1), TODAY)
        assert year.fuel_liters == 106 and year.fuel_cents == 19560
        assert costs.charging_estimate(session, golf, TODAY) is None
    page = client.get("/fuel").text
    assert "5.50 l/100 km" in page and "Migrol" in page
    assert "Carburant" in client.get("/").text


def test_sync_skips_offline_car_but_publishes_its_sensors(client, monkeypatch, no_network):
    add_golf(client)
    client.post("/fuel", data={"date": "2026-05-01", "km": "81000", "liters": "40", "amount": "70"})
    fetched = []
    monkeypatch.setattr(sync, "fetch_json", lambda url, token=None, timeout=20: fetched.append(url) or [])
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        sync.run_sync(session, NOW, ha=("http://supervisor/core/api", "token"))
        golf = session.query(Vehicle).filter_by(name="Golf").one()
        status = sync.get_settings(session, golf)
        assert status["sync_odometer"] == "voiture non connectée"
        assert status["sync_battery"] == "pas de batterie de traction"
    sent = {url.rsplit("/", 1)[1]: payload for url, payload in no_network}
    assert "sensor.frais_voiture_mois" in sent and "sensor.frais_voiture_golf_mois" in sent
    assert sent["sensor.frais_voiture_golf_mois"]["attributes"]["friendly_name"].startswith("Golf : ")
    assert sent["sensor.frais_voiture_golf_carburant"]["attributes"]["last_liters"] == 40
    assert sent["sensor.frais_voiture_golf_vehicule"]["attributes"]["connected"] is False
    assert "sensor.frais_voiture_golf_batterie" not in sent
    assert "sensor.frais_voiture_batterie" in sent  # la Citigo garde ses capteurs


def test_archived_car_is_not_synced_and_delete_needs_its_name(client):
    add_golf(client)
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        golf = session.query(Vehicle).filter_by(name="Golf").one()
        session.add(Expense(vehicle_id=golf.id, date=TODAY, amount_cents=100))
        session.commit()
    client.post(f"/vehicles/{golf.id}/archive")
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        assert session.get(Vehicle, golf.id).archived is True
    client.post(f"/vehicles/{golf.id}/delete", data={"confirm": "golf"})
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        assert session.get(Vehicle, golf.id) is not None
    client.post(f"/vehicles/{golf.id}/delete", data={"confirm": "Golf"})
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        assert session.get(Vehicle, golf.id) is None
        assert session.query(Expense).count() == 0


def test_existing_single_car_database_still_works(client):
    # Une base d'avant la 0.6 : motorisation et connexion vides.
    with make_sessionmaker(make_engine(client.tmp_path))() as session:
        citigo = session.query(Vehicle).one()
        assert citigo.energy_name == "électrique" and citigo.plugs_in and citigo.is_connected
    page = client.get("/").text
    assert "/charging" in page and 'name="vehicle"' not in page
