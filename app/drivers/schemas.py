from pydantic import BaseModel


class DriverRead(BaseModel):
    id: int
    whatsapp_phone: str
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
    executor_type: str | None
    employment_type: str | None
    hired_at: str | None
    is_hearing_impaired: str | None
    state: str

    model_config = {"from_attributes": True}
