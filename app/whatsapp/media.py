import httpx

from app.config import get_settings


class WhatsAppMediaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch_media(self, media_id: str | None) -> tuple[bytes, str | None]:
        if not media_id:
            raise ValueError("media_id is required")
        if not self.settings.whatsapp_access_token:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not configured")
        with httpx.Client(base_url=self.settings.whatsapp_api_base_url, timeout=30) as client:
            metadata = client.get(
                f"/{media_id}",
                headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
            )
            metadata.raise_for_status()
            metadata_json = metadata.json()
            media_url = metadata_json.get("url")
            if not media_url:
                raise RuntimeError("WhatsApp media URL is missing in response")
            media_response = client.get(
                media_url,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
            )
            media_response.raise_for_status()
            return media_response.content, metadata_json.get("mime_type")

    def download_media(self, media_id: str | None) -> bytes:
        content, _mime_type = self.fetch_media(media_id)
        return content
