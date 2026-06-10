import base64
import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "http://localhost:8000"
    strict_config: bool = False
    database_url: str = Field(default="sqlite:///./taxi_ai_manager.db", alias="DATABASE_URL")
    admin_username: str = "admin"
    admin_password: str = Field(default="changeme-admin", alias="ADMIN_PASSWORD")
    admin_session_secret: str = Field(default="changeme-session-secret", alias="ADMIN_SESSION_SECRET")
    admin_session_cookie_name: str = "sd_family_admin_session"
    admin_login_rate_limit_attempts: int = 10
    admin_login_rate_limit_window_seconds: int = 900

    public_site_brand_name: str = "SD Family Taxi"
    public_site_legal_name: str = "SD FAMILY, IP"
    public_site_support_email: str = "sdfamily@list.ru"
    public_site_support_phone: str = "+77071870107"
    public_site_whatsapp_phone: str = "+77766170666"
    public_site_address: str = "Астана, Алматинский район, Балкантау 117"
    public_site_oked: str = "49320 Деятельность такси"
    public_site_description: str = "Подключение водителей к таксопарку и регистрация через WhatsApp."

    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    ai_provider: str = "openai"
    llm_mode: str = "faq_only"
    llm_faq_assist_enabled: bool = False
    openai_model: str = "gpt-4o-mini"
    gemini_model: str = "gemini-2.5-flash"

    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_verify_token: str = "changeme"
    whatsapp_api_base_url: str = "https://graph.facebook.com/v20.0"

    google_service_account_json: str | None = None
    google_service_account_json_base64: str | None = None
    google_drive_folder_id: str | None = None
    google_sheets_id: str | None = None
    google_sheets_worksheet_name: str = "Applications"
    google_sheets_deletion_worksheet_name: str = "DeletionRequests"

    yandex_park_id: str | None = None
    yandex_client_id: str | None = None
    yandex_api_key: str | None = None
    yandex_api_base_url: str = "https://fleet-api.taxi.yandex.net"
    yandex_api_timeout_seconds: int = 30
    yandex_api_request_delay_seconds: float = 1.0
    yandex_car_catalog_cache_ttl_seconds: int = 86400
    yandex_driver_profile_work_rule_id: str | None = None
    yandex_driver_profile_category: str = "B"
    yandex_driver_profile_license_country: str = "kaz"
    yandex_car_brand: str | None = None
    yandex_car_model: str | None = None
    yandex_car_color: str | None = None
    yandex_car_year: str | None = None
    yandex_car_transmission: str = "automatic"
    yandex_car_fuel_type: str = "petrol"
    yandex_car_category: str | None = "econom"
    yandex_car_vin: str | None = None
    yandex_car_body_number: str | None = None
    yandex_car_sts_number: str | None = None
    yandex_car_license_plate: str | None = None
    yandex_car_rear_license_plate: str | None = None
    yandex_car_registration_certificate: str | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    def get_google_service_account_info(self) -> dict[str, object] | None:
        if self.google_service_account_json:
            raw = self.google_service_account_json.strip()
            if raw.startswith("{"):
                return json.loads(raw)
        if self.google_service_account_json_base64:
            decoded = base64.b64decode(self.google_service_account_json_base64).decode("utf-8")
            return json.loads(decoded)
        return None

    def missing_config(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}

        whatsapp_required = [
            "whatsapp_access_token",
            "whatsapp_phone_number_id",
            "whatsapp_verify_token",
        ]
        google_required = [
            "google_drive_folder_id",
            "google_sheets_id",
        ]
        yandex_required = [
            "yandex_park_id",
            "yandex_client_id",
            "yandex_api_key",
            "yandex_driver_profile_work_rule_id",
        ]
        ai_required: list[str] = []
        if self.ai_provider == "openai":
            ai_required = ["openai_api_key", "openai_model"]
        elif self.ai_provider == "gemini":
            ai_required = ["gemini_api_key", "gemini_model"]

        missing_whatsapp = [name for name in whatsapp_required if not getattr(self, name)]
        if missing_whatsapp:
            groups["whatsapp"] = missing_whatsapp

        missing_google = [name for name in google_required if not getattr(self, name)]
        if not self.get_google_service_account_info():
            missing_google.append("google_service_account_json or google_service_account_json_base64")
        if missing_google:
            groups["google"] = missing_google

        missing_yandex = [name for name in yandex_required if not getattr(self, name)]
        if missing_yandex:
            groups["yandex"] = missing_yandex

        missing_ai = [name for name in ai_required if not getattr(self, name)]
        if missing_ai:
            groups["ai"] = missing_ai

        return groups


@lru_cache
def get_settings() -> Settings:
    return Settings()
