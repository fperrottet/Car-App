from datetime import date

from app.costs import depreciation, due_amount, due_dates, km_between
from app.models import Contract, Vehicle
from app.util import add_months, format_money, parse_money


def test_parse_and_format_money():
    assert parse_money("1’234.50") == 123450
    assert parse_money("1'234,50") == 123450
    assert parse_money(" 89.9 ") == 8990
    assert parse_money("") is None
    assert format_money(123450) == "1’234.50"
    assert format_money(-500) == "-5.00"


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_due_dates_stop_at_today_and_contract_end():
    contract = Contract(start_date=date(2026, 1, 15), amount_cents=10000, period="trimestriel")
    assert due_dates(contract, date(2026, 10, 1)) == [date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15)]
    contract.end_date = date(2026, 7, 15)
    assert due_dates(contract, date(2026, 12, 31)) == [date(2026, 1, 15), date(2026, 4, 15)]
    contract.period = None
    assert due_dates(contract, date(2026, 12, 31)) == []


def test_km_between_uses_readings_around_the_period():
    points = [(date(2025, 6, 1), 1000), (date(2025, 12, 31), 6000), (date(2026, 6, 30), 11000)]
    assert km_between(points, date(2026, 1, 1), date(2026, 12, 31)) == 5000
    assert km_between(points, date(2025, 6, 1), date(2026, 6, 30)) == 10000
    assert km_between(points[:1], date(2026, 1, 1), date(2026, 12, 31)) is None


def test_depreciation_is_linear_over_the_chosen_years():
    vehicle = Vehicle(
        purchase_date=date(2026, 1, 1), purchase_price_cents=2_000_000, residual_value_cents=0, depreciation_years=10
    )
    one_year = depreciation(vehicle, date(2026, 1, 1), date(2026, 12, 31))
    assert abs(one_year - 200_000) < 200
    assert depreciation(vehicle, date(2025, 1, 1), date(2025, 12, 31)) == 0
    assert depreciation(vehicle, date(2040, 1, 1), date(2040, 12, 31)) == 0


def test_last_instalment_is_prorated_when_the_contract_ends_early():
    contract = Contract(start_date=date(2025, 1, 1), amount_cents=73000, period="annuel")
    assert due_amount(contract, date(2026, 1, 1)) == 73000
    contract.end_date = date(2026, 12, 31)
    assert due_amount(contract, date(2026, 1, 1)) == 73000
    contract.end_date = date(2026, 6, 30)
    assert due_dates(contract, date(2026, 12, 31)) == [date(2025, 1, 1), date(2026, 1, 1)]
    assert due_amount(contract, date(2025, 1, 1)) == 73000
    assert due_amount(contract, date(2026, 1, 1)) == 36200  # 181 jours sur 365
