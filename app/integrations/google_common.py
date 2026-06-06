from functools import lru_cache

from google.oauth2.service_account import Credentials

from app.config import get_settings


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


@lru_cache
def get_google_credentials() -> Credentials:
    settings = get_settings()
    service_account_info = settings.get_google_service_account_info()
    if not service_account_info:
        raise RuntimeError("Google service account credentials are not configured")
    return Credentials.from_service_account_info(service_account_info, scopes=GOOGLE_SCOPES)
