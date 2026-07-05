import re


MOJIBAKE_MARKERS = (
    "Р ",
    "РЎ",
    "СЂ",
    "Сџ",
    "рџ",
    "вЂ",
    "Ѓ",
    "�",
)

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
MOJIBAKE_RE = re.compile(r"(?:Р.|С.|р.|вЂ.|Ѓ|�)")


def looks_like_mojibake(value: str) -> bool:
    if not value:
        return False
    if any(marker in value for marker in MOJIBAKE_MARKERS):
        return True
    return bool(MOJIBAKE_RE.search(value))


def _score_text(value: str) -> tuple[int, int, int]:
    bad_markers = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    bad_pairs = len(MOJIBAKE_RE.findall(value))
    replacement_count = value.count("�")
    cyrillic_count = len(CYRILLIC_RE.findall(value))
    return (bad_markers + bad_pairs, replacement_count, -cyrillic_count)


def _repair_once(value: str) -> str:
    candidates = [value]
    for source_encoding in ("cp1251", "latin1"):
        try:
            repaired = value.encode(source_encoding, errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidates.append(repaired)
    return min(candidates, key=_score_text)


def repair_mojibake(value: str) -> str:
    if not value or not looks_like_mojibake(value):
        return value

    candidates = [value]

    repaired_full = _repair_once(value)
    candidates.append(repaired_full)
    if looks_like_mojibake(repaired_full):
        candidates.append(_repair_once(repaired_full))

    parts = re.split(r"(\s+)", value)
    repaired_parts = []
    for part in parts:
        if not part or part.isspace():
            repaired_parts.append(part)
            continue
        repaired_parts.append(_repair_once(part) if looks_like_mojibake(part) else part)
    candidates.append("".join(repaired_parts))

    return min(candidates, key=_score_text)
