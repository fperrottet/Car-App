from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import costs, tires
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import Expense, TirePair, Vehicle

TODAY = date(2026, 11, 14)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, today=lambda: TODAY, sync_interval=None)
    with TestClient(app) as client:
        client.post("/vehicle", data={"name": "Golf", "energy": "essence", "connected": "0",
                                      "purchase_date": "2026-01-01", "purchase_odometer_km": "80000"})
        client.tmp_path = tmp_path
        yield client


def db(client):
    return make_sessionmaker(make_engine(client.tmp_path))()


def test_estimated_fills_follow_the_km_of_each_month():
    fills = costs.estimate_fills(date(2026, 1, 1), date(2026, 3, 31), 80000, 80900, 6.0, 1.80, {})
    assert [f.date for f in fills] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert sum(f.distance for f in fills) == 900 and fills[-1].km == 80900
    assert round(sum(f.liters for f in fills), 1) == 54.0
    assert all(f.price == 1.80 for f in fills)
    # Le prix des mois précédents suit l'indice de l'essence.
    index = {"2026-01": 100.0, "2026-02": 100.0, "2026-03": 110.0}
    fills = costs.estimate_fills(date(2026, 1, 1), date(2026, 3, 31), 80000, 80900, 6.0, 1.98, index)
    assert [f.price for f in fills] == [1.8, 1.8, 1.98]
    with pytest.raises(ValueError):
        costs.estimate_fills(date(2026, 3, 1), date(2026, 1, 1), 80000, 80900, 6.0, 1.8, {})


def test_estimating_past_fills_then_deleting_them(client):
    client.post("/fuel", data={"date": "2026-06-01", "km": "84000", "liters": "40", "amount": "72"})
    page = client.get("/fuel").text
    assert 'name="end" value="2026-05-31"' in page and 'value="80000"' in page
    res = client.post("/fuel/estimate", data={"start": "2026-01-01", "end": "2026-05-31", "km_start": "80000",
                                              "km_end": "84000", "l100": "6", "price": "1.80"})
    assert res.status_code in (200, 303)
    with db(client) as session:
        golf = session.query(Vehicle).one()
        estimated = session.query(Expense).filter(Expense.estimated.is_(True)).all()
        assert len(estimated) == 5 and round(sum(e.fuel_liters for e in estimated)) == 240
        assert all(e.category and e.category.name == "Carburant" for e in estimated)
        fills = costs.fuel_log(session, golf)
        assert costs.fuel_average(fills) is None  # les estimations ne faussent pas la moyenne mesurée
    assert "estimé" in client.get("/fuel").text
    # Une période qui recouvre un plein existant est refusée.
    res = client.post("/fuel/estimate", data={"start": "2026-05-01", "end": "2026-07-01", "km_start": "83000",
                                              "km_end": "85000", "l100": "6", "price": "1.80"})
    assert "msg=" in res.headers.get("location", "") or "Il y a déjà" in res.text
    client.post("/fuel/estimate/delete")
    with db(client) as session:
        assert session.query(Expense).filter(Expense.fuel_liters.is_not(None)).count() == 1


def test_worn_out_tyres_are_listed_last(client):
    for season, day in (("été", "2024-03-01"), ("hiver", "2024-10-28")):
        client.post("/tires/new", data={"season": season, "count": "4", "purchase_date": day})
    with db(client) as session:
        summer = [p for p in session.query(TirePair) if p.tire_set.season == "été"]
        for p in summer:
            p.retired_date = date(2025, 10, 1)
        session.query(TirePair).filter(TirePair.name == "A", TirePair.id.in_([p.id for p in session.query(TirePair) if p.tire_set.season == "hiver"])).update({"retired_date": date(2025, 10, 1)}, synchronize_session=False)
        session.commit()
        golf = session.query(Vehicle).one()
        data = tires.timeline(session, golf, TODAY)
    assert [s["season"] for s in data["sets"]] == ["hiver", "été"]
    assert [p["name"] for p in data["sets"][0]["pairs"]] == ["B", "A"]
    assert [s["color"] for s in data["sets"]] == [1, 0]


def test_real_fill_after_estimates_is_not_measured_against_them(client):
    client.post("/fuel", data={"date": "2026-06-01", "km": "84000", "liters": "40", "amount": "72"})
    client.post("/fuel", data={"date": "2026-07-01", "km": "84700", "liters": "42", "amount": "75"})
    client.post("/fuel/estimate", data={"start": "2026-01-01", "end": "2026-05-31", "km_start": "80000",
                                        "km_end": "83990", "l100": "6", "price": "1.80"})
    with db(client) as session:
        fills = costs.fuel_log(session, session.query(Vehicle).one())
    assert fills[1].distance is None and fills[0].distance == 700
    assert costs.fuel_average(fills) == 6.0
