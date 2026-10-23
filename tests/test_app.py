from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TODAY = date(2026, 10, 23)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, today=lambda: TODAY, sync_interval=None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def car(client):
    response = client.post(
        "/vehicle",
        data={
            "name": "Citigo",
            "purchase_date": "2025-10-23",
            "purchase_price": "20'000",
            "purchase_odometer_km": "10",
            "depreciation_years": "10",
            "residual_value": "0",
            "odometer_entity": "sensor.216980_odometer",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def test_first_visit_asks_for_the_vehicle(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/vehicle"
    assert "sensor.216980_odometer" in client.get("/vehicle").text


def test_links_follow_the_ingress_prefix(client):
    prefix = "/api/hassio_ingress/abc123"
    response = client.get("/", headers={"X-Ingress-Path": prefix}, follow_redirects=False)
    assert response.headers["location"] == f"{prefix}/vehicle"
    page = client.get("/vehicle", headers={"X-Ingress-Path": prefix}).text
    assert f'href="{prefix}/static/style.css"' in page


def test_expense_with_invoice(car):
    response = car.post(
        "/expenses/new",
        data={"date": "2026-03-10", "amount": "149,90", "vendor": "Pneus Express", "odometer_km": "5000"},
        files={"file": ("facture.pdf", b"%PDF-1.4 test", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = car.get("/expenses").text
    assert "Pneus Express" in listing and "149.90" in listing
    documents = car.get("/documents").text
    assert "facture.pdf" in documents
    assert car.get("/documents/1/file").content == b"%PDF-1.4 test"
    csv = car.get("/expenses.csv").text
    assert "2026-03-10;149.90" in csv


def test_invalid_amount_shows_an_error(car):
    response = car.post("/expenses/new", data={"date": "2026-03-10", "amount": "abc"})
    assert response.status_code == 200
    assert "Montant invalide" in response.text


def test_contract_generates_its_due_dates(car):
    response = car.post(
        "/contracts/new",
        data={
            "kind": "Assurance",
            "provider": "Mobilière",
            "start_date": "2026-01-01",
            "amount": "300",
            "period": "trimestriel",
            "category_id": "1",
            "notice_days": "90",
            "end_date": "2026-12-31",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = car.get("/expenses").text
    # Échéances de janvier, avril, juillet et octobre 2026.
    assert listing.count("Mobilière") == 4
    assert "1’200.00" in listing

    # Modifier le contrat recalcule les échéances sans doublon.
    car.post(
        "/contracts/1",
        data={"kind": "Assurance", "provider": "Mobilière", "start_date": "2026-01-01", "amount": "1200",
              "period": "annuel", "category_id": "1", "end_date": "2026-12-31"},
    )
    listing = car.get("/expenses").text
    assert listing.count("Mobilière") == 1

    dashboard = car.get("/").text
    assert "Fin du contrat" in dashboard


def test_dashboard_cost_per_km(car):
    car.post("/vehicle/readings", data={"date": "2026-01-01", "km": "4010"})
    car.post("/vehicle/readings", data={"date": "2026-10-23", "km": "14010"})
    car.post("/expenses/new", data={"date": "2026-05-01", "amount": "1000", "category_id": "4"})
    page = car.get("/?period=year").text
    assert "10’000 km parcourus" in page
    assert "1’000.00" in page  # Entretien


def test_maintenance_creates_linked_expense_and_reminder(car):
    car.post(
        "/maintenance/new",
        data={"date": "2026-09-01", "odometer_km": "14000", "garage": "Garage du Lac", "work": "Service annuel",
              "amount": "350", "next_km": "14500"},
    )
    assert "Garage du Lac" in car.get("/expenses").text
    assert "Service dans 500 km" in car.get("/").text
    car.post("/maintenance/1/delete")
    assert "Garage du Lac" not in car.get("/expenses").text


def test_every_page_renders(car):
    car.post("/expenses/new", data={"date": "2026-03-10", "amount": "10"},
             files={"file": ("ticket.jpg", b"jpg", "image/jpeg")})
    car.post("/contracts/new", data={"kind": "Garantie", "provider": "Skoda", "start_date": "2025-10-23"})
    car.post("/maintenance/new", data={"date": "2026-09-01", "work": "Pneus hiver"})
    for path in ["/", "/?period=12m", "/?period=all", "/?period=2025", "/expenses", "/expenses/new",
                 "/expenses/1", "/contracts", "/contracts/new", "/contracts/1", "/maintenance",
                 "/maintenance/new", "/maintenance/1", "/documents", "/documents?q=ticket", "/categories", "/vehicle"]:
        assert car.get(path).status_code == 200, path
    response = car.post("/documents/1/delete", data={"back": "/expenses/1"}, follow_redirects=False)
    assert response.headers["location"] == "/expenses/1"
    assert "ticket.jpg" not in car.get("/documents").text
