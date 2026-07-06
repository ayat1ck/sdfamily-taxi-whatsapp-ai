from __future__ import annotations


class MissingFieldsCalculator:
    REQUIRED_DOCUMENTS = (
        "driver_license",
        "vehicle_registration_doc",
    )
    DRIVER_FIELDS = (
        "full_name",
        "iin",
        "birth_date",
        "driver_license_number",
        "driver_license_issue_date",
        "driver_license_expires_at",
        "driving_experience_since",
    )
    VEHICLE_FIELDS = (
        "brand",
        "model",
        "year",
        "plate_number",
        "color",
        "registration_certificate",
    )

    def calculate(self, draft: dict) -> list[str]:
        missing: list[str] = []
        documents = draft.get("documents", {})
        document_flags = self._document_flags(documents)

        for key in self.REQUIRED_DOCUMENTS:
            if not document_flags.get(key):
                missing.append(key)

        driver = draft.get("driver", {})
        vehicle = draft.get("vehicle", {})
        for field in self.DRIVER_FIELDS:
            if not driver.get(field):
                missing.append(field)
        for field in self.VEHICLE_FIELDS:
            if not vehicle.get(field):
                missing.append(field)
        is_complete = not missing
        draft["missing_fields"] = missing
        draft["is_registration_complete"] = is_complete
        draft["ready_for_yandex"] = is_complete
        return missing

    def _document_flags(self, documents: dict) -> dict[str, bool]:
        flags = {
            "driver_license": False,
            "id_card": False,
            "vehicle_registration_doc": False,
            "selfie_with_license": False,
        }
        for doc_type, payload in documents.items():
            if payload:
                flags[doc_type] = True
        return flags
