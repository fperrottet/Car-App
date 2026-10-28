from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import costs, sync, tires
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import TireMount, TirePair, TirePeriod, TireSet, Vehicle

TODAY = date(2026, 10, 23)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, today=lambda: TODAY, sync_interval=None)
    with TestClient(app) as client:
        client.post("/vehicle", data={"name": "Citigo", "purchase_date": "2022-02-21", "purchase_odometer_km": "14400"})
        client.tmp_path = tmp_path
        yield client


def db(client):
    return make_sessionmaker(make_engine(client.tmp_path))()


def buy(client, **fields):
    data = {"season": "été", "count": "4", "size": "165/70 R14", "purchase_date": "2022-02-21"} | fields
    assert client.post("/tires/new", data=data).status_code in (200, 303)


def act(client, action, **body):
    return client.post(f"/tires/timeline/{action}", json=body)


def lanes(client):
    with db(client) as session:
        vehicle = session.query(Vehicle).one()
        return {
            axle: [(p.pair.tire_set.season, p.pair.name, p.start_date.isoformat(), p.end_date and p.end_date.isoformat())
                   for p in tires.lane(session, vehicle, axle)]
            for axle in ("avant", "arriere")
        }


def pair_ids(client):
    with db(client) as session:
        return {(p.tire_set.season, p.tire_set.count, p.name): p.id for p in session.query(TirePair)}


def test_four_tyres_make_two_pairs_mounted_front_and_rear(client):
    buy(client, mount_now="1", mount_date="2022-02-21", amount="480")
    assert lanes(client) == {"avant": [("été", "A", "2022-02-21", None)], "arriere": [("été", "B", "2022-02-21", None)]}
    assert "480.00" in client.get("/expenses").text


def test_dragging_a_pair_mounts_it_and_sends_the_other_to_the_garage(client):
    buy(client, mount_now="1", mount_date="2022-02-21")
    buy(client, season="hiver", purchase_date="2022-10-28")
    ids = pair_ids(client)
    out = act(client, "mount", pair=ids[("hiver", 4, "A")], axle="avant", date="2022-11-05").json()
    assert "retourne au garage" in out["message"]
    assert lanes(client)["avant"] == [("été", "A", "2022-02-21", "2022-11-05"), ("hiver", "A", "2022-11-05", None)]
    # La même paire ne peut pas être à l'avant et à l'arrière.
    res = act(client, "mount", pair=ids[("hiver", 4, "A")], axle="arriere", date="2022-12-01")
    assert res.status_code == 400 and "déjà montée" in res.json()["error"]


def test_season_change_then_moving_the_date_moves_both_axles(client):
    buy(client, mount_now="1", mount_date="2022-02-21")
    buy(client, season="hiver", purchase_date="2022-10-28")
    assert act(client, "season", date="2022-11-05").status_code == 200
    front = lanes(client)["avant"]
    assert front[-1] == ("hiver", "A", "2022-11-05", None)
    with db(client) as session:
        vehicle = session.query(Vehicle).one()
        winter_front = tires.lane(session, vehicle, "avant")[-1].id
    out = act(client, "move", period=winter_front, date="2022-11-19", linked=True).json()
    assert "avant et arrière" in out["message"]
    got = lanes(client)
    assert got["avant"][0][3] == "2022-11-19" and got["arriere"][1][2] == "2022-11-19"
    # Pas au-delà de la période voisine.
    assert act(client, "move", period=winter_front, date="2022-01-01", linked=True).status_code == 400


def test_half_of_a_set_is_retired_and_a_new_pair_replaces_it(client):
    client.post("/vehicle/readings", data={"date": "2025-04-12", "km": "26000"})
    client.post("/vehicle/readings", data={"date": "2026-10-20", "km": "33700"})
    buy(client, mount_now="1", mount_date="2022-02-21")
    buy(client, count="2", position="avant", purchase_date="2025-04-10", brand="Continental")
    ids = pair_ids(client)
    retired = act(client, "retire", pair=ids[("été", 4, "A")], date="2025-04-12", tread_mm="2,8")
    assert retired.status_code == 200, retired.text
    act(client, "mount", pair=ids[("été", 2, "A")], axle="avant", date="2025-04-12")
    got = lanes(client)
    assert got["avant"] == [("été", "A", "2022-02-21", "2025-04-12"), ("été", "A", "2025-04-12", None)]
    assert got["arriere"] == [("été", "B", "2022-02-21", None)]  # l'autre paire du lot reste montée
    with db(client) as session:
        old = session.get(TirePair, ids[("été", 4, "A")])
        assert old.retired_date == date(2025, 4, 12) and tires.last_tread(old) == 2.8
        vehicle = session.query(Vehicle).one()
        points = costs.odometer_points(session, vehicle)
        assert tires.pair_km(old, points, costs.current_km(session, vehicle)) == 26000 - 14400
        new = session.get(TirePair, ids[("été", 2, "A")])
        assert tires.pair_km(new, points, costs.current_km(session, vehicle)) == 33700 - 26000
    # Une paire éliminée ne se remonte pas après son élimination.
    assert act(client, "mount", pair=ids[("été", 4, "A")], axle="arriere", date="2026-01-01").status_code == 400


def test_deleting_a_period_gives_the_place_back_to_the_previous_one(client):
    buy(client, mount_now="1", mount_date="2022-02-21")
    buy(client, season="hiver", purchase_date="2022-10-28")
    ids = pair_ids(client)
    act(client, "mount", pair=ids[("hiver", 4, "A")], axle="avant", date="2022-11-05")
    with db(client) as session:
        winter = session.query(TirePeriod).filter_by(pair_id=ids[("hiver", 4, "A")]).one().id
    act(client, "delete", period=winter)
    assert lanes(client)["avant"] == [("été", "A", "2022-02-21", None)]


def test_low_tread_and_season_reminders(client):
    buy(client, mount_now="1", mount_date="2026-04-01")
    buy(client, season="hiver", brand="Nokian", purchase_date="2025-10-01")
    ids = pair_ids(client)
    act(client, "mount", pair=ids[("hiver", 4, "B")], axle="arriere", date="2025-11-01")
    with db(client) as session:
        period = session.query(TirePeriod).filter_by(pair_id=ids[("hiver", 4, "B")]).one()
    act(client, "end", period=period.id, date="2026-03-30", tread_mm="3.5")
    page = client.get("/").text
    assert "penser aux pneus d&#39;hiver" in page or "penser aux pneus d'hiver" in page
    assert "3.5 mm mesurés" in page


def test_tyre_page_and_card_sensor(client):
    buy(client, mount_now="1", mount_date="2022-02-21", brand="Michelin")
    page = client.get("/tires").text
    assert 'id="tl-data"' in page and "Michelin" in page
    assert client.get("/tires/1").status_code == 200
    with db(client) as session:
        vehicle = session.query(Vehicle).one()
        attrs = sync.ha_sensors(session, vehicle, TODAY)["vehicule"][4]
    assert [t["position"] for t in attrs["tires"]] == ["avant", "arrière"]


def test_tyres_of_another_car_cannot_be_changed(client):
    buy(client, mount_now="1", mount_date="2022-02-21")
    ids = pair_ids(client)
    client.post("/vehicle", data={"id": "new", "name": "Golf", "energy": "essence", "connected": "0"})
    res = act(client, "retire", pair=ids[("été", 4, "A")], date="2025-01-01")
    assert res.status_code == 400 and "introuvables" in res.json()["error"]


def test_old_tyre_sets_are_migrated_to_pairs(tmp_path):
    create_app(tmp_path, sync_interval=None)
    session = make_sessionmaker(make_engine(tmp_path))()
    vehicle = Vehicle(name="Citigo")
    session.add(vehicle)
    session.flush()
    four = TireSet(vehicle_id=vehicle.id, season="été", count=4, position="les 4")
    four.mounts = [TireMount(mounted_date=date(2022, 2, 21), mounted_km=14400, removed_date=date(2022, 11, 5),
                             removed_km=17000, tread_mm=5.0)]
    front = TireSet(vehicle_id=vehicle.id, season="été", count=2, position="avant")
    front.mounts = [TireMount(mounted_date=date(2025, 4, 12))]
    gone = TireSet(vehicle_id=vehicle.id, season="hiver", count=4, position="les 4", retired_date=date(2024, 4, 1))
    session.add_all([four, front, gone])
    session.commit()
    assert tires.migrate(session) == 3
    assert [(p.name, [(q.axle, q.start_km, q.end_km, q.tread_mm) for q in p.periods]) for p in four.pairs] == [
        ("A", [("avant", 14400, 17000, 5.0)]), ("B", [("arriere", 14400, 17000, 5.0)])]
    assert [(p.name, [q.axle for q in p.periods]) for p in front.pairs] == [("A", ["avant"])]
    assert all(p.retired_date == date(2024, 4, 1) for p in gone.pairs)
    assert tires.migrate(session) == 0  # une seule fois
    session.close()
