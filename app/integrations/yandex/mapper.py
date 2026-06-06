from app.drivers.models import Driver
from app.integrations.yandex.schemas import YandexDriverPayload


def map_driver_to_yandex(driver: Driver) -> YandexDriverPayload:
    vehicle = driver.vehicle
    return YandexDriverPayload(
        full_name=driver.full_name,
        last_name=driver.last_name,
        first_name=driver.first_name,
        middle_name=driver.middle_name,
        phone=driver.phone,
        city=driver.city,
        address=driver.address,
        iin=driver.iin,
        birth_date=driver.birth_date,
        driving_experience_since=driver.driving_experience_since,
        driver_license_number=driver.driver_license_number,
        driver_license_issue_date=driver.driver_license_issue_date,
        driver_license_expires_at=driver.driver_license_expires_at,
        executor_type=driver.executor_type,
        employment_type=driver.employment_type,
        hired_at=driver.hired_at,
        existing_vehicle_lookup=None,
        has_personal_car="true",
        is_hearing_impaired=driver.is_hearing_impaired,
        car_brand=vehicle.brand if vehicle else None,
        car_model=vehicle.model if vehicle else None,
        car_year=vehicle.year if vehicle else None,
        plate_number=vehicle.plate_number if vehicle else None,
        color=vehicle.color if vehicle else None,
        vin=vehicle.vin if vehicle else None,
    )
