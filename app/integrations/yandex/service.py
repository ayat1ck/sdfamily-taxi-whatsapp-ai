from datetime import datetime

from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver
from app.integrations.yandex.client import YandexFleetClient, YandexPartialSubmissionError
from app.integrations.yandex.catalog import get_yandex_car_catalog
from app.integrations.yandex.mapper import map_driver_to_yandex
from app.integrations.yandex.messages import format_validation_errors_for_user
from app.utils.validators import (
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
            application.status = "sent_to_yandex"
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
