import re


MOJIBAKE_MARKERS = ("Р", "СЃ", "С‚", "вЂ", "пё", "рџ")


def looks_like_mojibake(value: str) -> bool:
    if not value:
        return False
    return any(marker in value for marker in MOJIBAKE_MARKERS)


def repair_mojibake(value: str) -> str:
    if not value or not looks_like_mojibake(value):
        return value

    def _repair_chunk(chunk: str) -> str:
        candidates = [chunk]
        for source_encoding in ("cp1251", "latin1"):
            try:
                repaired = chunk.encode(source_encoding, errors="strict").decode("utf-8", errors="strict")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidates.append(repaired)
            try:
                repaired_twice = repaired.encode(source_encoding, errors="strict").decode("utf-8", errors="strict")
            except (UnicodeEncodeError, UnicodeDecodeError):
                repaired_twice = repaired
            candidates.append(repaired_twice)
        return min(candidates, key=lambda item: sum(item.count(marker) for marker in MOJIBAKE_MARKERS))

    parts = re.split(r"(\s+)", value)
    repaired_parts = []
    for part in parts:
        if not part or part.isspace():
            repaired_parts.append(part)
            continue
        repaired_parts.append(_repair_chunk(part) if looks_like_mojibake(part) else part)
    return "".join(repaired_parts)
