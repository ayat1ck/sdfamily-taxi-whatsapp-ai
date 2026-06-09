from datetime import datetime

from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver
from app.integrations.yandex.client import YandexFleetClient, YandexPartialSubmissionError
from app.integrations.yandex.mapper import map_driver_to_yandex


class YandexSubmissionService:
    def __init__(self) -> None:
        self.client = YandexFleetClient()

    def submit(self, db: Session, driver: Driver, application: Application) -> Application:
        payload = map_driver_to_yandex(driver)
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
        return self.client.build_submission_preview(payload)
