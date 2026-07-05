import httpx

from app.config import get_settings
from app.utils.logger import get_logger


logger = get_logger(__name__)


class WhatsAppSender:
    def __init__(self) -> None:
        self.settings = get_settings()

    def send_text(self, phone: str, text: str) -> dict[str, object]:
        return self.send_payload(
            {
                "messaging_product": "whatsapp",
                "to": phone.lstrip("+"),
                "type": "text",
                "text": {"body": text},
            }
        )

    def send_payload(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.settings.whatsapp_access_token or not self.settings.whatsapp_phone_number_id:
            raise RuntimeError("WhatsApp sender is not configured")
        with httpx.Client(base_url=self.settings.whatsapp_api_base_url, timeout=30) as client:
            response = client.post(
                f"/{self.settings.whatsapp_phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {self.settings.whatsapp_access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        logger.info("Sent WhatsApp message to %s", payload.get("to"))
        return result
