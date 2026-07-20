from types import SimpleNamespace
from unittest.mock import patch

from app.integrations.yandex.client import YandexFleetClient
from app.integrations.yandex.schemas import YandexDriverPayload


def _payload(**overrides) -> YandexDriverPayload:
    base = dict(
        full_name="Ivanov Ivan",
        last_name="Ivanov",
        first_name="Ivan",
        middle_name=None,
        phone="+77001112233",
        city="Astana",
        address=None,
        iin="900101300123",
        birth_date="1990-01-01",
        driving_experience_since="2015-01-01",
        driver_license_number="AB123456",
        driver_license_issue_date="2015-01-01",
        driver_license_expires_at="2030-01-01",
        executor_type=None,
        employment_type="самозанятый",
        hired_at="2026-01-01",
        existing_vehicle_lookup=None,
        has_personal_car="true",
        is_hearing_impaired="false",
        car_brand="Toyota",
        car_model="Camry",
        car_year="2018",
        plate_number="123ABC01",
        color="белый",
        service_class=None,
        registration_certificate="AA12345678",
        vin="XTA217030E0458846",
    )
    base.update(overrides)
    return YandexDriverPayload(**base)


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        yandex_car_brand=None,
        yandex_car_model=None,
        yandex_car_color=None,
        yandex_car_year=None,
        yandex_car_license_plate=None,
        yandex_car_transmission="mechanical",
        yandex_car_vin=None,
        yandex_car_body_number=None,
        yandex_car_registration_certificate=None,
        yandex_car_sts_number=None,
        yandex_car_fuel_type="petrol",
        yandex_car_category=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_vin_is_also_sent_as_body_number() -> None:
    client = YandexFleetClient()
    with patch.object(client, "settings", _settings()), patch.object(
        client, "_normalize_vehicle_color", side_effect=lambda value: value
    ):
        vehicle = client._build_vehicle_payload(_payload())

    specs = vehicle["vehicle_specifications"]
    assert specs["vin"] == "XTA217030E0458846"
    assert specs["body_number"] == "XTA217030E0458846"


def test_explicit_body_number_setting_overrides_vin() -> None:
    client = YandexFleetClient()
    with patch.object(client, "settings", _settings(yandex_car_body_number="BODYONLY123")), patch.object(
        client, "_normalize_vehicle_color", side_effect=lambda value: value
    ):
        vehicle = client._build_vehicle_payload(_payload())

    specs = vehicle["vehicle_specifications"]
    assert specs["vin"] == "XTA217030E0458846"
    assert specs["body_number"] == "BODYONLY123"


def test_default_categories_include_all_passenger_tariffs() -> None:
    client = YandexFleetClient()
    with patch.object(
        client,
        "settings",
        _settings(yandex_car_category="econom,comfort,comfort_plus,business,express,intercity"),
    ), patch.object(client, "_normalize_vehicle_color", side_effect=lambda value: value):
        vehicle = client._build_vehicle_payload(_payload(service_class=None))

    assert vehicle["park_profile"]["categories"] == [
        "econom",
        "comfort",
        "comfort_plus",
        "business",
        "express",
        "intercity",
    ]


def test_payload_service_class_overrides_default_categories() -> None:
    client = YandexFleetClient()
    with patch.object(
        client,
        "settings",
        _settings(yandex_car_category="econom,comfort"),
    ), patch.object(client, "_normalize_vehicle_color", side_effect=lambda value: value):
        vehicle = client._build_vehicle_payload(_payload(service_class="econom"))

    assert vehicle["park_profile"]["categories"] == ["econom"]
