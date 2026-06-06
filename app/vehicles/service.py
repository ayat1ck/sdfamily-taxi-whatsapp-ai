from sqlalchemy import select
from sqlalchemy.orm import Session

from app.drivers.models import Driver
from app.vehicles.models import Vehicle
from app.utils.validators import normalize_plate_number


def get_or_create_vehicle(db: Session, driver: Driver) -> Vehicle:
    if driver.vehicle:
        return driver.vehicle
    vehicle = Vehicle(driver_id=driver.id)
    db.add(vehicle)
    db.flush()
    return vehicle


def find_vehicle_by_plate_number(db: Session, plate_number: str, exclude_driver_id: int | None = None) -> Vehicle | None:
    normalized_plate = normalize_plate_number(plate_number)
    vehicles = db.scalars(select(Vehicle).where(Vehicle.plate_number.is_not(None))).all()
    for vehicle in vehicles:
        if normalize_plate_number(vehicle.plate_number or "") != normalized_plate:
            continue
        if exclude_driver_id is not None and vehicle.driver_id == exclude_driver_id:
            continue
        return vehicle
    return None
