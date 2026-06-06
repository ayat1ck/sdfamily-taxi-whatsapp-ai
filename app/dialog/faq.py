from pathlib import Path


KB_DIR = Path(__file__).resolve().parents[2] / "knowledge_base"


def load_knowledge_base() -> dict[str, str]:
    data: dict[str, str] = {}
    if not KB_DIR.exists():
        return data
    for file_path in KB_DIR.glob("*.md"):
        data[file_path.stem] = file_path.read_text(encoding="utf-8")
    return data


def find_faq_answer(message: str, kb: dict[str, str]) -> str | None:
    lowered = message.lower()
    for _, content in kb.items():
        for line in content.splitlines():
            if line.startswith("Q:") and line[2:].strip().lower() in lowered:
                return content
    keyword_map = {
        "какие документы": "documents",
        "яндекс про": "yandex_pro",
        "без своего авто": "registration",
        "какие авто": "car_requirements",
        "статус заявки": "registration",
    }
    for keyword, doc_name in keyword_map.items():
        if keyword in lowered and doc_name in kb:
            return kb[doc_name]
    return None
