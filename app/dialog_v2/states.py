from __future__ import annotations


class DialogV2State:
    NEW = "new"
    REGISTRATION_DOCUMENT_COLLECTION = "registration_document_collection"
    REGISTRATION_MISSING_FIELDS = "registration_missing_fields"
    REGISTRATION_CONFIRMATION = "registration_confirmation"
    READY_TO_SEND_YANDEX = "ready_to_send_yandex"
    MANAGER_HANOFF = "manager_handoff"
    COMPLETED = "completed"
