import json
import sys
from pathlib import Path


def _incoming_texts(chat: dict) -> list[str]:
    messages = chat.get("messages") or []
    result: list[str] = []
    for message in messages:
        if message.get("direction") != "incoming":
            continue
        if message.get("message_type") != "text":
            continue
        text = (message.get("text") or "").strip()
        if text:
            result.append(text)
    return result


def _build_cases(payload: dict) -> list[dict]:
    chats = payload.get("chats") or []
    cases: list[dict] = []

    for chat in chats:
        phone = ((chat.get("driver") or {}).get("whatsapp_phone") or "").strip()
        incoming = _incoming_texts(chat)
        if not phone or len(incoming) < 2:
            continue

        joined = " ".join(incoming).lower()
        exact_two_index = next((idx for idx, text in enumerate(incoming) if text.strip() == "2"), None)
        case: dict | None = None

        if exact_two_index is not None and exact_two_index > 0:
            case = {
                "name": f"export_menu_option_two_{phone[-4:]}",
                "phone": phone,
                "messages": [incoming[exact_two_index - 1], incoming[exact_two_index]],
                "expected_failure": True,
                "expected": {
                    "final_state": "new",
                    "application_status": "collecting_data",
                    "reply_contains": [{"step": 1, "text": "2%"}],
                    "reply_not_contains": [{"step": 1, "text": "Не могу точно определить проблему"}],
                },
            }
        elif "за 200 заказов" in joined or "20 тысяч" in joined or "бонус" in joined:
            prompt = next((text for text in incoming if any(marker in text.lower() for marker in ("за 200", "20 тысяч", "бонус"))), None)
            if prompt:
                case = {
                    "name": f"export_bonus_question_{phone[-4:]}",
                    "phone": phone,
                    "messages": [prompt],
                    "expected": {
                        "final_state": "new",
                        "application_status": "collecting_data",
                        "reply_contains": [{"step": 0, "text": "5000"}],
                        "reply_not_contains": [{"step": 0, "text": "После входа в Яндекс Про"}],
                    },
                }
        elif any(marker in joined for marker in ("жазылайн", "тіркел", "тыркел")):
            prompt = next((text for text in incoming if any(marker in text.lower() for marker in ("жазылайн", "тіркел", "тыркел"))), None)
            if prompt:
                case = {
                    "name": f"export_kazakh_registration_{phone[-4:]}",
                    "phone": phone,
                    "messages": [prompt],
                    "expected_failure": True,
                    "expected": {
                        "final_state": "ask_full_name",
                    },
                }

        if case and case["name"] not in {existing["name"] for existing in cases}:
            cases.append(case)

    return cases


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: generate_chat_fixtures.py <input_chats.json> <output_fixture.json>")
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    cases = _build_cases(payload)
    output = {"generated_from": str(input_path), "cases": cases}
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(cases)} cases into {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
