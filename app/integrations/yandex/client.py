from uuid import uuid4

import httpx

from app.config import get_settings
from app.integrations.yandex.schemas import YandexDriverPayload
from app.utils.validators import normalize_work_rule_id


class YandexFleetClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def submit_driver(self, payload: YandexDriverPayload) -> dict[str, str]:
        if not payload.phone or not payload.driver_license_number:
            raise ValueError("Missing required Yandex driver fields")
        self._validate_config()

        headers = self._build_headers()

        with httpx.Client(base_url=self.settings.yandex_api_base_url, timeout=self.settings.yandex_api_timeout_seconds) as client:
            driver_response = client.post(
                "/v2/parks/contractors/driver-profile",
                headers=headers,
                json=self._build_driver_payload(payload),
            )
            self._raise_for_status(driver_response)
            driver_json = driver_response.json()

            vehicle_response = client.post(
                "/v2/parks/vehicles/car",
                headers=headers,
                json=self._build_vehicle_payload(payload),
            )
            self._raise_for_status(vehicle_response)
            vehicle_json = vehicle_response.json()

            vehicle_id = self._extract_vehicle_id(vehicle_json)
            if vehicle_id:
                bind_response = client.put(
                    "/v1/parks/driver-profiles/car-bindings",
                    headers=headers,
                    params=self._build_binding_params(driver_json, vehicle_id),
                )
                self._raise_for_status(bind_response)

        return {
            "status": "sent_to_yandex",
            "yandex_driver_id": self._extract_driver_id(driver_json),
            "yandex_vehicle_id": vehicle_id,
        }

    def build_submission_preview(self, payload: YandexDriverPayload) -> dict[str, object]:
        self._validate_config()
        return {
            "driver_payload": payload.model_dump(),
            "headers": self._build_headers(preview=True),
            "requests": {
                "create_driver_profile": {
                    "method": "POST",
                    "path": "/v2/parks/contractors/driver-profile",
                    "json": self._build_driver_payload(payload),
                },
                "create_vehicle": {
                    "method": "POST",
                    "path": "/v2/parks/vehicles/car",
                    "json": self._build_vehicle_payload(payload),
                },
                "bind_vehicle": {
                    "method": "PUT",
                    "path": "/v1/parks/driver-profiles/car-bindings",
                    "params": {
                        "park_id": self.settings.yandex_park_id,
                        "driver_profile_id": "<returned_contractor_profile_id>",
                        "car_id": "<returned_vehicle_id>",
                    },
                },
            },
        }

    def _build_headers(self, preview: bool = False) -> dict[str, str]:
        return {
            "X-Client-ID": self.settings.yandex_client_id or "",
            "X-API-Key": self.settings.yandex_api_key or "",
            "X-Park-ID": self.settings.yandex_park_id or "",
            "X-Idempotency-Token": "<generated-uuid4-hex>" if preview else uuid4().hex,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _validate_config(self) -> None:
        required_config = {
            "YANDEX_PARK_ID": self.settings.yandex_park_id,
            "YANDEX_CLIENT_ID": self.settings.yandex_client_id,
            "YANDEX_API_KEY": self.settings.yandex_api_key,
            "YANDEX_DRIVER_PROFILE_WORK_RULE_ID": normalize_work_rule_id(self.settings.yandex_driver_profile_work_rule_id),
        }
        missing = [name for name, value in required_config.items() if not value]
        if missing:
            raise ValueError(f"Missing Yandex configuration: {', '.join(missing)}")

    def _build_driver_payload(self, payload: YandexDriverPayload) -> dict[str, object]:
        work_rule_id = normalize_work_rule_id(self.settings.yandex_driver_profile_work_rule_id)
        return {
            "account": {
                "work_rule_id": work_rule_id,
            },
            "person": {
                "full_name": {
                    "first_name": payload.first_name or "",
                    "middle_name": payload.middle_name or "",
                    "last_name": payload.last_name or "",
                },
                "contact_info": {
                    "address": payload.address or "",
                    "phone": payload.phone or "",
                },
                "driver_license": {
                    "birth_date": payload.birth_date,
                    "country": self.settings.yandex_driver_profile_license_country,
                    "expiry_date": payload.driver_license_expires_at,
                    "issue_date": payload.driver_license_issue_date,
                    "number": payload.driver_license_number,
                },
                "driver_license_experience": {
                    "total_since_date": payload.driving_experience_since,
                },
                "tax_identification_number": payload.iin or "",
                "employment_type": self._map_employment_type(payload.employment_type),
            },
            "profile": {
                "hire_date": payload.hired_at,
                "comment": self._build_comment(payload),
            },
        }

    def _build_vehicle_payload(self, payload: YandexDriverPayload) -> dict[str, object]:
        brand = payload.car_brand or self.settings.yandex_car_brand
        model = payload.car_model or self.settings.yandex_car_model
        color = self._normalize_vehicle_color(payload.color or self.settings.yandex_car_color)
        year = int(payload.car_year or self.settings.yandex_car_year or 0)
        plate_number = payload.plate_number or self.settings.yandex_car_license_plate

        vehicle_specifications: dict[str, object] = {
            "brand": brand,
            "model": model,
            "color": color,
            "year": year,
            "transmission": self.settings.yandex_car_transmission,
        }
        if payload.vin or self.settings.yandex_car_vin:
            vehicle_specifications["vin"] = payload.vin or self.settings.yandex_car_vin
        if self.settings.yandex_car_body_number:
            vehicle_specifications["body_number"] = self.settings.yandex_car_body_number

        vehicle_licenses: dict[str, object] = {
            "licence_plate_number": plate_number,
        }
        if self.settings.yandex_car_registration_certificate:
            vehicle_licenses["registration_certificate"] = self.settings.yandex_car_registration_certificate
        if self.settings.yandex_car_sts_number:
            vehicle_licenses["licence_number"] = self.settings.yandex_car_sts_number

        park_profile: dict[str, object] = {
            "callsign": plate_number,
            "status": "working",
            "comment": f"driver_phone={payload.phone}" if payload.phone else "",
            "fuel_type": self.settings.yandex_car_fuel_type,
        }
        if self.settings.yandex_car_category:
            park_profile["categories"] = [self.settings.yandex_car_category]

        return {
            "vehicle_specifications": vehicle_specifications,
            "vehicle_licenses": vehicle_licenses,
            "park_profile": park_profile,
        }

    def _build_binding_params(self, driver_json: dict, vehicle_id: str) -> dict[str, object]:
        return {
            "park_id": self.settings.yandex_park_id,
            "driver_profile_id": self._extract_driver_id(driver_json),
            "car_id": vehicle_id,
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        message = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                code = payload.get("code")
                detail = payload.get("message") or payload.get("detail")
                if not detail and isinstance(payload.get("details"), list):
                    detail = "; ".join(str(item) for item in payload["details"])
                if code or detail:
                    message = f"Yandex API error {response.status_code}: code={code}, message={detail}"
        except Exception:
            message = None
        if not message:
            message = f"Yandex API error {response.status_code}: {response.text}"
        raise ValueError(message)

    @staticmethod
    def _normalize_vehicle_color(value: str | None) -> str | None:
        normalized = (value or "").strip().lower()
        if not normalized:
            return value
        mapping = {
            "white": "Белый",
            "beliy": "Белый",
            "белый": "Белый",
            "yellow": "Желтый",
            "zheltyi": "Желтый",
            "желтый": "Желтый",
            "beige": "Бежевый",
            "beigee": "Бежевый",
            "бежевый": "Бежевый",
            "black": "Черный",
            "chernyi": "Черный",
            "черный": "Черный",
            "light blue": "Голубой",
            "goluboi": "Голубой",
            "голубой": "Голубой",
            "gray": "Серый",
            "grey": "Серый",
            "seryi": "Серый",
            "серый": "Серый",
            "red": "Красный",
            "krasnyi": "Красный",
            "красный": "Красный",
            "orange": "Оранжевый",
            "oranzhevyi": "Оранжевый",
            "оранжевый": "Оранжевый",
            "blue": "Синий",
            "sinii": "Синий",
            "синий": "Синий",
            "green": "Зеленый",
            "zelenyi": "Зеленый",
            "зеленый": "Зеленый",
            "brown": "Коричневый",
            "korichnevyi": "Коричневый",
            "коричневый": "Коричневый",
            "purple": "Фиолетовый",
            "fioletovyi": "Фиолетовый",
            "фиолетовый": "Фиолетовый",
            "pink": "Розовый",
            "rozovyi": "Розовый",
            "розовый": "Розовый",
        }
        return mapping.get(normalized, value)

    @staticmethod
    def _map_employment_type(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        mapping = {
            "штатный": "park_employee",
            "shtatnyi": "park_employee",
            "shtatniy": "park_employee",
            "shtatnyy": "park_employee",
            "staff": "park_employee",
            "employee": "park_employee",
            "самозанятый": "selfemployed",
            "samozanyatyi": "selfemployed",
            "samozanyatiy": "selfemployed",
            "self employed": "selfemployed",
            "self-employed": "selfemployed",
            "ип": "individual_entrepreneur",
            "individual entrepreneur": "individual_entrepreneur",
        }
        return mapping.get(normalized, "park_employee")

    @staticmethod
    def _build_comment(payload: YandexDriverPayload) -> str:
        parts = []
        if payload.city:
            parts.append(f"city={payload.city}")
        if payload.is_hearing_impaired is not None:
            parts.append(f"is_hearing_impaired={payload.is_hearing_impaired}")
        return "; ".join(parts)

    @staticmethod
    def _extract_driver_id(response_json: dict) -> str:
        return str(
            response_json.get("contractor_profile_id")
            or response_json.get("id")
            or response_json.get("driver_profile", {}).get("id")
            or response_json.get("driver_profile_id")
            or ""
        )

    @staticmethod
    def _extract_vehicle_id(response_json: dict) -> str:
        return str(
            response_json.get("id")
            or response_json.get("vehicle", {}).get("id")
            or response_json.get("vehicle_id")
            or ""
        )
