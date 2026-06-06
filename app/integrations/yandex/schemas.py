from pydantic import BaseModel


class YandexDriverPayload(BaseModel):
    full_name: str | None
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    phone: str | None
    city: str | None
    address: str | None
    iin: str | None
    birth_date: str | None
    driving_experience_since: str | None
    driver_license_number: str | None
    driver_license_issue_date: str | None
    driver_license_expires_at: str | None
    executor_type: str | None
    employment_type: str | None
    hired_at: str | None
    existing_vehicle_lookup: str | None
    has_personal_car: str | None
    is_hearing_impaired: str | None
    car_brand: str | None
    car_model: str | None
    car_year: str | None
    plate_number: str | None
    color: str | None
    vin: str | None = None
