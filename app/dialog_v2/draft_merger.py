from __future__ import annotations

from dataclasses import dataclass


def _pick_higher(current: dict, key: str, value, confidence_by_field: dict[str, float], confidence: float) -> bool:
    existing_conf = confidence_by_field.get(key, -1.0)
    if value is None:
        return False
    if current.get(key) is not None and existing_conf > confidence:
        return False
    current[key] = value
    confidence_by_field[key] = confidence
    return True


@dataclass(slots=True)
class DraftMergeResult:
    draft: dict[str, object]
    updated_fields: list[str]


class DraftMerger:
    DRIVER_FIELDS = {
        "full_name",
        "iin",
        "birth_date",
        "driver_license_number",
        "driver_license_issue_date",
        "driver_license_expires_at",
        "driving_experience_since",
    }
    VEHICLE_FIELDS = {
        "brand",
        "model",
        "year",
        "plate_number",
        "color",
        "registration_certificate",
        "vin",
    }

    def merge(
        self,
        *,
        current_draft: dict,
        document_type: str,
        extracted_fields: dict[str, str],
        confidence: float,
    ) -> DraftMergeResult:
        draft = current_draft
        draft.setdefault("driver", {})
        draft.setdefault("vehicle", {})
        draft.setdefault("documents", {})
        draft.setdefault("missing_fields", [])
        draft.setdefault("confidence_by_field", {})
        draft.setdefault("document_confidence_by_type", {})

        updated_fields: list[str] = []
        confidence_by_field = draft["confidence_by_field"]

        if document_type in draft["documents"]:
            draft["documents"][document_type] = {
                "received": True,
                "confidence": confidence,
                "fields": sorted(extracted_fields.keys()),
            }
            draft["document_confidence_by_type"][document_type] = confidence

        if document_type == "selfie_with_license":
            return DraftMergeResult(draft=draft, updated_fields=updated_fields)

        for key, value in extracted_fields.items():
            if key in self.DRIVER_FIELDS:
                if _pick_higher(draft["driver"], key, value, confidence_by_field, confidence):
                    updated_fields.append(key)
            elif key in self.VEHICLE_FIELDS:
                if _pick_higher(draft["vehicle"], key, value, confidence_by_field, confidence):
                    updated_fields.append(key)
            else:
                draft.setdefault("extra_fields", {})[key] = value

        return DraftMergeResult(draft=draft, updated_fields=updated_fields)
