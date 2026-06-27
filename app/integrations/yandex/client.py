from uuid import uuid4
import re
import time

import httpx

from app.config import get_settings
from app.integrations.yandex.schemas import YandexDriverPayload
from app.utils.validators import normalize_phone, normalize_work_rule_id, split_service_class_values


class YandexPartialSubmissionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        yandex_driver_id: str = "",
        yandex_vehicle_id: str = "",
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.yandex_driver_id = yandex_driver_id
        self.yandex_vehicle_id = yandex_vehicle_id


class YandexFleetClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def submit_driver(self, payload: YandexDriverPayload) -> dict[str, str]:
        if not payload.phone or not payload.driver_license_number:
            raise ValueError("Missing required Yandex driver fields")
        self._validate_config()

        headers = self._build_headers()
        driver_json: dict[str, object] = {}
        vehicle_json: dict[str, object] = {}
        driver_id = ""
        vehicle_id = ""

        try:
            with httpx.Client(base_url=self.settings.yandex_api_base_url, timeout=self.settings.yandex_api_timeout_seconds) as client:
                driver_response = client.post(
                    "/v2/parks/contractors/driver-profile",
                    headers=headers,
                    json=self._build_driver_payload(payload),
                )
                self._raise_for_status(driver_response)
                driver_json = driver_response.json()
                driver_id = self._extract_driver_id(driver_json)

                self._wait_between_requests()

                vehicle_response = client.post(
                    "/v2/parks/vehicles/car",
                    headers=headers,
                    json=self._build_vehicle_payload(payload),
                )
                self._raise_for_status(vehicle_response)
                vehicle_json = vehicle_response.json()
                vehicle_id = self._extract_vehicle_id(vehicle_json)

                if vehicle_id:
                    self._wait_between_requests()
                    bind_response = client.put(
                        "/v1/parks/driver-profiles/car-bindings",
                        headers=headers,
                        params=self._build_binding_params(driver_json, vehicle_id),
                    )
                    self._raise_for_status(bind_response)
        except Exception as exc:
            if driver_id or vehicle_id:
                stage = "driver_created"
                if driver_id and vehicle_id:
                    stage = "bind_failed"
                raise YandexPartialSubmissionError(
                    str(exc),
                    stage=stage,
                    yandex_driver_id=driver_id,
                    yandex_vehicle_id=vehicle_id,
                ) from exc
            raise

        return {
            "status": "sent_to_yandex",
            "yandex_driver_id": driver_id,
            "yandex_vehicle_id": vehicle_id,
        }

    def submit_vehicle_and_bind(self, payload: YandexDriverPayload, driver_profile_id: str) -> dict[str, str]:
        if not driver_profile_id:
            raise ValueError("Missing existing Yandex driver profile id")
        self._validate_config()
        headers = self._build_headers()
        vehicle_id = ""

        try:
            with httpx.Client(base_url=self.settings.yandex_api_base_url, timeout=self.settings.yandex_api_timeout_seconds) as client:
                vehicle_response = client.post(
                    "/v2/parks/vehicles/car",
                    headers=headers,
                    json=self._build_vehicle_payload(payload),
                )
                self._raise_for_status(vehicle_response)
                vehicle_json = vehicle_response.json()
                vehicle_id = self._extract_vehicle_id(vehicle_json)

                self._wait_between_requests()
                bind_response = client.put(
                    "/v1/parks/driver-profiles/car-bindings",
                    headers=headers,
                    params={
                        "park_id": self.settings.yandex_park_id,
                        "driver_profile_id": driver_profile_id,
                        "car_id": vehicle_id,
                    },
                )
                self._raise_for_status(bind_response)
        except Exception as exc:
            raise YandexPartialSubmissionError(
                str(exc),
                stage="vehicle_or_bind_failed",
                yandex_driver_id=driver_profile_id,
                yandex_vehicle_id=vehicle_id,
            ) from exc

        return {
            "status": "sent_to_yandex",
            "yandex_driver_id": driver_profile_id,
            "yandex_vehicle_id": vehicle_id,
        }

    def bind_driver_to_vehicle(self, driver_profile_id: str, vehicle_id: str) -> dict[str, str]:
        if not driver_profile_id or not vehicle_id:
            raise ValueError("Missing Yandex driver or vehicle id for binding")
        self._validate_config()
        headers = self._build_headers()
        with httpx.Client(base_url=self.settings.yandex_api_base_url, timeout=self.settings.yandex_api_timeout_seconds) as client:
            bind_response = client.put(
                "/v1/parks/driver-profiles/car-bindings",
                headers=headers,
                params={
                    "park_id": self.settings.yandex_park_id,
                    "driver_profile_id": driver_profile_id,
                    "car_id": vehicle_id,
                },
            )
            self._raise_for_status(bind_response)
        return {
            "status": "sent_to_yandex",
            "yandex_driver_id": driver_profile_id,
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

    def fetch_cars_catalog(self) -> object:
        self._validate_config()
        headers = self._build_headers()
        with httpx.Client(
            base_url=self.settings.yandex_api_base_url,
            timeout=self.settings.yandex_api_timeout_seconds,
        ) as client:
            response = client.get("/v1/parks/cars/catalog", headers=headers)
            self._raise_for_status(response)
            return response.json()

    def find_driver_profile(self, lookup: str, *, limit: int = 10) -> dict[str, object] | None:
        self._validate_config()
        normalized_lookup = self._normalize_lookup(lookup)
        if not normalized_lookup:
            return None

        base_body = {
            "limit": max(1, min(limit, 100)),
            "offset": 0,
            "fields": {
                "driver_profile": ["id", "park_id", "created_date", "work_status"],
                "account": ["id", "balance", "balance_limit", "currency"],
                "person": [
                    "full_name",
                    "contact_info",
                    "driver_license",
                    "tax_identification_number",
                    "employment_type",
                ],
                "car": ["id", "brand", "model", "license_plate_number", "callsign"],
            },
        }
        request_variants = [
            (
                "/v1/parks/driver-profiles/list",
                {"park_id": self.settings.yandex_park_id},
                {**base_body, "query": {"text": normalized_lookup}},
            ),
            (
                "/v1/parks/driver-profiles/list",
                {},
                {**base_body, "query": {"park": {"id": self.settings.yandex_park_id}, "text": normalized_lookup}},
            ),
            (
                "/v1/parks/contractors/driver-profiles/list",
                {"park_id": self.settings.yandex_park_id},
                {**base_body, "query": {"text": normalized_lookup}},
            ),
        ]
        headers = self._build_headers()
        payload: object | None = None
        last_error: Exception | None = None
        with httpx.Client(base_url=self.settings.yandex_api_base_url, timeout=self.settings.yandex_api_timeout_seconds) as client:
            for path, params, body in request_variants:
                try:
                    response = client.post(path, headers=headers, params=params, json=body)
                    self._raise_for_status(response)
                    payload = response.json()
                    break
                except Exception as exc:
                    last_error = exc
        if payload is None:
            if last_error:
                raise last_error
            return None

        candidates = self._extract_list_items(payload)
        if not candidates:
            return None
        return self._choose_driver_profile_candidate(candidates, normalized_lookup)

    def _build_headers(self, preview: bool = False) -> dict[str, str]:
        return {
            "X-Client-ID": self.settings.yandex_client_id or "",
            "X-API-Key": self.settings.yandex_api_key or "",
            "X-Park-ID": self.settings.yandex_park_id or "",
            "X-Idempotency-Token": "<generated-uuid4-hex>" if preview else uuid4().hex,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "ru",
        }

    def _wait_between_requests(self) -> None:
        delay = max(float(self.settings.yandex_api_request_delay_seconds), 0.0)
        if delay:
            time.sleep(delay)

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
                    "category": self.settings.yandex_driver_profile_category,
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
        registration_certificate = payload.registration_certificate or self.settings.yandex_car_registration_certificate
        sts_number = registration_certificate or self.settings.yandex_car_sts_number
        if registration_certificate:
            vehicle_licenses["registration_certificate"] = registration_certificate
        if sts_number:
            vehicle_licenses["licence_number"] = sts_number

        park_profile: dict[str, object] = {
            "callsign": plate_number,
            "status": "working",
            "comment": f"driver_phone={payload.phone}" if payload.phone else "",
            "fuel_type": self.settings.yandex_car_fuel_type,
        }
        categories = split_service_class_values(payload.service_class or "")
        if not categories and self.settings.yandex_car_category:
            categories = [self.settings.yandex_car_category]
        if categories:
            park_profile["categories"] = categories

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
        raw = (value or "").strip()
        normalized = raw.lower()
        if not normalized:
            return value
        candidates = [part.strip() for part in re.split(r"[\/,;|]+", normalized) if part.strip()]
        if not candidates:
            candidates = [normalized]
        mapping = {
            "white": "Белый",
            "beliy": "Белый",
            "ақ": "Белый",
            "ак": "Белый",
            "белый": "Белый",
            "yellow": "Желтый",
            "zheltyi": "Желтый",
            "сары": "Желтый",
            "желтый": "Желтый",
            "beige": "Бежевый",
            "beigee": "Бежевый",
            "бежевый": "Бежевый",
            "black": "Черный",
            "chernyi": "Черный",
            "қара": "Черный",
            "кара": "Черный",
            "черный": "Черный",
            "light blue": "Голубой",
            "goluboi": "Голубой",
            "голубой": "Голубой",
            "gray": "Серый",
            "grey": "Серый",
            "seryi": "Серый",
            "сұр": "Серый",
            "сур": "Серый",
            "серый": "Серый",
            "red": "Красный",
            "krasnyi": "Красный",
            "қызыл": "Красный",
            "кызыл": "Красный",
            "красный": "Красный",
            "orange": "Оранжевый",
            "oranzhevyi": "Оранжевый",
            "оранжевый": "Оранжевый",
            "blue": "Синий",
            "sinii": "Синий",
            "көк": "Синий",
            "kok": "Синий",
            "синий": "Синий",
            "green": "Зеленый",
            "zelenyi": "Зеленый",
            "жасыл": "Зеленый",
            "зеленый": "Зеленый",
            "brown": "Коричневый",
            "korichnevyi": "Коричневый",
            "қоңыр": "Коричневый",
            "konyr": "Коричневый",
            "коричневый": "Коричневый",
            "purple": "Фиолетовый",
            "fioletovyi": "Фиолетовый",
            "фиолетовый": "Фиолетовый",
            "pink": "Розовый",
            "rozovyi": "Розовый",
            "розовый": "Розовый",
        }
        for candidate in candidates:
            if candidate in mapping:
                return mapping[candidate]
        return mapping.get(normalized, raw)

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
        if payload.document_refs:
            docs = "; ".join(
                f"{document.get('document_type')}={document.get('url')}"
                for document in payload.document_refs
                if document.get("document_type") and document.get("url")
            )
            if docs:
                parts.append(f"documents={docs}")
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

    @staticmethod
    def _normalize_lookup(value: str) -> str:
        raw = (value or "").strip()
        digits = re.sub(r"\D+", "", raw)
        if len(digits) in {10, 11, 12}:
            if len(digits) == 12:
                return digits
            return normalize_phone(digits)
        return raw

    @classmethod
    def _extract_list_items(cls, payload: object) -> list[dict[str, object]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("driver_profiles", "drivers", "contractors", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in payload.values():
            nested = cls._extract_list_items(value)
            if nested:
                return nested
        return []

    @classmethod
    def _choose_driver_profile_candidate(
        cls,
        candidates: list[dict[str, object]],
        lookup: str,
    ) -> dict[str, object]:
        lookup_digits = re.sub(r"\D+", "", lookup)
        for candidate in candidates:
            flattened = cls._flatten_values(candidate)
            candidate_digits = [re.sub(r"\D+", "", value) for value in flattened]
            if lookup_digits and any(lookup_digits and lookup_digits in digits for digits in candidate_digits):
                return candidate
            normalized_phone = normalize_phone(lookup) if lookup_digits else ""
            if normalized_phone and any(normalize_phone(value) == normalized_phone for value in flattened if re.search(r"\d", value)):
                return candidate
        return candidates[0]

    @classmethod
    def _flatten_values(cls, value: object) -> list[str]:
        if isinstance(value, dict):
            values: list[str] = []
            for nested in value.values():
                values.extend(cls._flatten_values(nested))
            return values
        if isinstance(value, list):
            values = []
            for nested in value:
                values.extend(cls._flatten_values(nested))
            return values
        if value is None:
            return []
        return [str(value)]
