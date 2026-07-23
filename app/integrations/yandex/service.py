from datetime import datetime
import re

from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver
from app.integrations.yandex.client import YandexFleetClient, YandexPartialSubmissionError
from app.integrations.yandex.catalog import get_yandex_car_catalog
from app.integrations.yandex.mapper import map_driver_to_yandex
from app.integrations.yandex.messages import format_validation_errors_for_user
from app.vehicles.service import get_or_create_vehicle
from app.utils.validators import (
    normalize_phone,
    validate_birth_date,
    validate_driver_dates,
    validate_driver_license_number,
    validate_hired_at,
    validate_kz_iin,
)


class YandexSubmissionService:
    def __init__(self) -> None:
        self.client = YandexFleetClient()

    def submit(self, db: Session, driver: Driver, application: Application) -> Application:
        payload = map_driver_to_yandex(driver)
        validation = self.validate_payload(payload)
        if validation["errors"]:
            raise ValueError(self._format_validation_failure(validation["errors"]))

        try:
            if application.yandex_driver_id and application.yandex_vehicle_id:
                result = self.client.bind_driver_to_vehicle(application.yandex_driver_id, application.yandex_vehicle_id)
            elif application.yandex_driver_id:
                result = self.client.submit_vehicle_and_bind(payload, application.yandex_driver_id)
            else:
                result = self.client.submit_driver(payload)
        except YandexPartialSubmissionError as exc:
            application.status = "yandex_error"
            application.yandex_status = "partial_success"
            application.yandex_driver_id = exc.yandex_driver_id or application.yandex_driver_id
            application.yandex_vehicle_id = exc.yandex_vehicle_id or application.yandex_vehicle_id
            application.yandex_error = str(exc)
            application.sent_to_yandex_at = datetime.utcnow()
            db.add(application)
            db.flush()
            raise

        application.status = "sent_to_yandex"
        application.yandex_status = result["status"]
        application.yandex_driver_id = result.get("yandex_driver_id") or application.yandex_driver_id
        application.yandex_vehicle_id = result.get("yandex_vehicle_id") or application.yandex_vehicle_id
        application.yandex_error = None
        application.sent_to_yandex_at = datetime.utcnow()
        db.add(application)
        db.flush()
        return application

    def validate_driver(self, driver: Driver) -> dict[str, list[str]]:
        payload = map_driver_to_yandex(driver)
        return self.validate_payload(payload)

    def preview(self, driver: Driver) -> dict[str, object]:
        payload = map_driver_to_yandex(driver)
        validation = self.validate_payload(payload)
        preview = self.client.build_submission_preview(payload)
        preview["validation"] = validation
        preview["document_refs"] = payload.document_refs or []
        return preview

    def find_and_sync_existing_driver(self, db: Session, driver: Driver, lookup: str) -> Driver | None:
        profile = self.client.find_driver_profile(lookup)
        if not profile:
            return None

        application = driver.applications[0] if driver.applications else Application(driver_id=driver.id)
        db.add(application)

        yandex_driver_id = self._extract_yandex_driver_id(profile)
        if yandex_driver_id:
            application.yandex_driver_id = yandex_driver_id
        application.yandex_status = "found_existing"
        application.status = "sent_to_yandex"
        application.yandex_error = None

        fields = self._extract_profile_fields(profile)
        for key, value in fields.items():
            if value and hasattr(driver, key):
                setattr(driver, key, value)

        vehicle_fields = self._extract_vehicle_fields(profile)
        yandex_vehicle_id = vehicle_fields.pop("yandex_vehicle_id", None)
        if yandex_vehicle_id:
            application.yandex_vehicle_id = yandex_vehicle_id
        if any(vehicle_fields.values()):
            vehicle = get_or_create_vehicle(db, driver)
            for key, value in vehicle_fields.items():
                if value and hasattr(vehicle, key):
                    setattr(vehicle, key, value)
            db.add(vehicle)

        driver.state = "completed"
        driver.dialog_mode = "bot_active"
        driver.requires_attention = False
        driver.updated_at = datetime.utcnow()
        db.add(driver)
        db.add(application)
        db.flush()
        return driver

    def update_vehicle_in_yandex(self, db: Session, driver: Driver, application: Application) -> dict[str, str]:
        """Push current local vehicle fields to an existing Yandex car."""
        if not application.yandex_vehicle_id:
            raise ValueError("missing_yandex_vehicle_id")
        payload = map_driver_to_yandex(driver)
        result = self.client.update_vehicle(application.yandex_vehicle_id, payload)
        application.yandex_status = result["status"]
        application.yandex_error = None
        application.updated_at = datetime.utcnow()
        db.add(application)
        db.flush()
        return result

    def add_vehicle_and_bind(self, db: Session, driver: Driver, application: Application) -> dict[str, str]:
        """Create a new car in the park and bind it to the existing driver profile."""
        if not application.yandex_driver_id:
            raise ValueError("missing_yandex_driver_id")
        payload = map_driver_to_yandex(driver)
        required = {
            "car_brand": payload.car_brand,
            "car_model": payload.car_model,
            "car_year": payload.car_year,
            "plate_number": payload.plate_number,
        }
        vehicle_errors = [f"missing:{key}" for key, value in required.items() if not value]
        if vehicle_errors:
            raise ValueError(self._format_validation_failure(vehicle_errors))
        result = self.client.submit_vehicle_and_bind(payload, application.yandex_driver_id)
        application.yandex_vehicle_id = result.get("yandex_vehicle_id") or application.yandex_vehicle_id
        application.yandex_status = result["status"]
        application.yandex_error = None
        application.sent_to_yandex_at = datetime.utcnow()
        db.add(application)
        db.flush()
        return result

    def validate_payload(self, payload) -> dict[str, list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        required_driver_fields = {
            "last_name": payload.last_name,
            "first_name": payload.first_name,
            "phone": payload.phone,
            "address": payload.address,
            "iin": payload.iin,
            "birth_date": payload.birth_date,
            "driving_experience_since": payload.driving_experience_since,
            "driver_license_number": payload.driver_license_number,
            "driver_license_issue_date": payload.driver_license_issue_date,
            "driver_license_expires_at": payload.driver_license_expires_at,
            "employment_type": payload.employment_type,
            "hired_at": payload.hired_at,
        }
        required_vehicle_fields = {
            "car_brand": payload.car_brand,
            "car_model": payload.car_model,
            "car_year": payload.car_year,
            "plate_number": payload.plate_number,
            "color": payload.color,
            "registration_certificate": payload.registration_certificate,
        }

        for field_name, value in required_driver_fields.items():
            if not value:
                errors.append(f"missing:{field_name}")
        for field_name, value in required_vehicle_fields.items():
            if not value:
                errors.append(f"missing:{field_name}")

        if payload.driver_license_issue_date and payload.driver_license_expires_at:
            if payload.driver_license_expires_at <= payload.driver_license_issue_date:
                errors.append("invalid:driver_license_expires_at_before_issue_date")
        if payload.birth_date and payload.driving_experience_since and payload.driving_experience_since < payload.birth_date:
            errors.append("invalid:driving_experience_before_birth_date")
        if payload.birth_date and payload.driving_experience_since and payload.driving_experience_since == payload.birth_date:
            errors.append("invalid:driving_experience_same_as_birth")
        if payload.hired_at and payload.driver_license_expires_at and payload.hired_at == payload.driver_license_expires_at:
            errors.append("invalid:hired_at_same_as_license_expiry")
        if payload.iin:
            errors.extend(f"invalid:{item}" for item in validate_kz_iin(payload.iin))
        if payload.birth_date:
            errors.extend(f"invalid:{item}" for item in validate_birth_date(payload.birth_date))
        if payload.driver_license_number:
            errors.extend(
                f"invalid:{item}" for item in validate_driver_license_number(payload.driver_license_number)
            )
        errors.extend(
            f"invalid:{item}"
            for item in validate_driver_dates(
                birth_date=payload.birth_date,
                driving_experience_since=payload.driving_experience_since,
                driver_license_issue_date=payload.driver_license_issue_date,
                driver_license_expires_at=payload.driver_license_expires_at,
            )
        )
        if payload.hired_at:
            errors.extend(f"invalid:{item}" for item in validate_hired_at(payload.hired_at))
        if payload.document_refs:
            warnings.append(f"documents_as_refs:{len(payload.document_refs)}")
        else:
            warnings.append("documents_missing")

        if payload.employment_type and payload.employment_type not in {"штатный", "самозанятый"}:
            warnings.append("employment_type_unrecognized")

        catalog = get_yandex_car_catalog()
        catalog_index = catalog.get_index()
        if catalog_index is not None:
            warnings.append(f"catalog_loaded:brands={len(catalog_index.brands)}:models={catalog_index.size}")
            _, _, catalog_errors = catalog.validate_pair(payload.car_brand, payload.car_model)
            errors.extend(catalog_errors)
        elif catalog.is_configured:
            warnings.append("catalog_unavailable:using_local_normalization")

        return {"errors": errors, "warnings": warnings}

    @staticmethod
    def _format_validation_failure(errors: list[str]) -> str:
        return "Yandex payload validation failed: " + "; ".join(str(item) for item in errors)

    @classmethod
    def _extract_profile_fields(cls, profile: dict[str, object]) -> dict[str, str]:
        person = cls._first_dict(profile, "person", "driver", "contractor", "profile") or profile
        full_name = cls._extract_full_name(person)
        phone = cls._first_string(person, "phone", "phone_number", "driver_phone")
        contact_info = cls._first_dict(person, "contact_info", "contacts") or {}
        phone = phone or cls._first_string(contact_info, "phone", "phone_number")
        iin = (
            cls._first_string(person, "tax_identification_number", "iin")
            or cls._first_string(profile, "tax_identification_number", "iin")
        )
        license_data = cls._first_dict(person, "driver_license", "license") or {}
        return {
            "full_name": full_name,
            "last_name": cls._first_string(person, "last_name"),
            "first_name": cls._first_string(person, "first_name"),
            "middle_name": cls._first_string(person, "middle_name"),
            "phone": normalize_phone(phone) if phone else "",
            "iin": re.sub(r"\D+", "", iin or "") if iin else "",
            "driver_license_number": cls._first_string(license_data, "number", "driver_license_number"),
            "driver_license_issue_date": cls._first_string(license_data, "issue_date"),
            "driver_license_expires_at": cls._first_string(license_data, "expiry_date", "expires_at"),
            "birth_date": cls._first_string(license_data, "birth_date") or cls._first_string(person, "birth_date"),
            "employment_type": cls._first_string(person, "employment_type"),
        }

    @classmethod
    def _extract_vehicle_fields(cls, profile: dict[str, object]) -> dict[str, str]:
        car = cls._first_dict(profile, "car", "vehicle") or {}
        return {
            "yandex_vehicle_id": cls._first_string(car, "id", "car_id", "vehicle_id"),
            "brand": cls._first_string(car, "brand"),
            "model": cls._first_string(car, "model"),
            "year": cls._first_string(car, "year"),
            "plate_number": cls._first_string(car, "license_plate_number", "licence_plate_number", "plate_number"),
            "color": cls._first_string(car, "color"),
            "vin": cls._first_string(car, "vin"),
        }

    @classmethod
    def _extract_yandex_driver_id(cls, profile: dict[str, object]) -> str:
        driver_profile = cls._first_dict(profile, "driver_profile", "contractor_profile") or {}
        return (
            cls._first_string(profile, "contractor_profile_id", "driver_profile_id", "id")
            or cls._first_string(driver_profile, "id", "driver_profile_id", "contractor_profile_id")
        )

    @classmethod
    def _extract_full_name(cls, source: dict[str, object]) -> str:
        full_name = source.get("full_name")
        if isinstance(full_name, str):
            return full_name.strip()
        if isinstance(full_name, dict):
            parts = [
                cls._first_string(full_name, "last_name"),
                cls._first_string(full_name, "first_name"),
                cls._first_string(full_name, "middle_name"),
            ]
            return " ".join(part for part in parts if part).strip()
        parts = [
            cls._first_string(source, "last_name"),
            cls._first_string(source, "first_name"),
            cls._first_string(source, "middle_name"),
        ]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _first_dict(source: dict[str, object], *keys: str) -> dict[str, object] | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _first_string(source: dict[str, object], *keys: str) -> str:
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""
