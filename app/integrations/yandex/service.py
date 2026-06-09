from datetime import datetime

from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver
from app.integrations.yandex.client import YandexFleetClient, YandexPartialSubmissionError
from app.integrations.yandex.mapper import map_driver_to_yandex
from app.utils.validators import validate_birth_date, validate_driver_dates, validate_hired_at, validate_kz_iin


class YandexSubmissionService:
    def __init__(self) -> None:
        self.client = YandexFleetClient()

    def submit(self, db: Session, driver: Driver, application: Application) -> Application:
        payload = map_driver_to_yandex(driver)
        validation = self.validate_payload(payload)
        if validation["errors"]:
            raise ValueError(
                "Yandex payload validation failed: " + "; ".join(str(item) for item in validation["errors"])
            )
        try:
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
        application.yandex_driver_id = result["yandex_driver_id"]
        application.yandex_vehicle_id = result["yandex_vehicle_id"]
        application.yandex_error = None
        application.sent_to_yandex_at = datetime.utcnow()
        db.add(application)
        db.flush()
        return application

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
        if payload.iin:
            errors.extend(f"invalid:{item}" for item in validate_kz_iin(payload.iin))
        if payload.birth_date:
            errors.extend(f"invalid:{item}" for item in validate_birth_date(payload.birth_date))
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

        return {"errors": errors, "warnings": warnings}
