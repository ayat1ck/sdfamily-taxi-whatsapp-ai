from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.integrations.yandex.client import YandexFleetClient
from app.utils.validators import (
    extract_known_car_brand,
    iter_car_model_normalize_candidates,
    normalize_car_brand,
    normalize_car_model,
    normalize_text_token,
    transliterate_cyrillic_to_latin,
)

logger = logging.getLogger(__name__)

_CATALOG_CACHE: dict[str, object] = {"loaded_at": 0.0, "entries": []}


@dataclass(frozen=True)
class CarCatalogEntry:
    brand: str
    model: str | None = None


@dataclass
class CarCatalogIndex:
    brands: dict[str, str] = field(default_factory=dict)
    models: dict[tuple[str, str], str] = field(default_factory=dict)
    brand_models: dict[str, list[str]] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.models)


def catalog_match_key(value: str) -> str:
    token = normalize_text_token(value)
    transliterated = transliterate_cyrillic_to_latin(token)
    return re.sub(r"[^a-z0-9]", "", transliterated.lower())


def parse_catalog_payload(payload: object) -> list[CarCatalogEntry]:
    entries: list[CarCatalogEntry] = []
    seen: set[tuple[str, str | None]] = set()

    def add_entry(brand: object, model: object | None = None) -> None:
        brand_text = str(brand or "").strip()
        model_text = str(model or "").strip() if model is not None else ""
        if not brand_text:
            return
        key = (brand_text, model_text or None)
        if key in seen:
            return
        seen.add(key)
        entries.append(CarCatalogEntry(brand=brand_text, model=model_text or None))

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        brand = node.get("brand") or node.get("mark") or node.get("name")
        model = node.get("model") or node.get("model_name")
        nested_models = node.get("models") or node.get("model_list")

        if brand and model:
            add_entry(brand, model)
        elif brand and nested_models:
            add_entry(brand, None)
            for nested in nested_models:
                if isinstance(nested, str):
                    add_entry(brand, nested)
                elif isinstance(nested, dict):
                    nested_model = nested.get("model") or nested.get("name") or nested.get("title")
                    add_entry(brand, nested_model)
        elif brand:
            add_entry(brand, None)

        for child_key in ("categories", "brands", "items", "cars", "values"):
            child = node.get(child_key)
            if child is not None:
                walk(child)

    walk(payload)
    return entries


def build_catalog_index(entries: list[CarCatalogEntry]) -> CarCatalogIndex:
    index = CarCatalogIndex()
    for entry in entries:
        brand_key = catalog_match_key(entry.brand)
        if brand_key and brand_key not in index.brands:
            index.brands[brand_key] = entry.brand
        if entry.model:
            model_key = catalog_match_key(entry.model)
            if brand_key and model_key:
                index.models[(brand_key, model_key)] = entry.model
                index.brand_models.setdefault(brand_key, [])
                if entry.model not in index.brand_models[brand_key]:
                    index.brand_models[brand_key].append(entry.model)
    return index


class YandexCarCatalog:
    def __init__(self, client: YandexFleetClient | None = None) -> None:
        self.client = client or YandexFleetClient()
        self.settings = get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.yandex_park_id
            and self.settings.yandex_client_id
            and self.settings.yandex_api_key
        )

    def get_index(self, *, force_refresh: bool = False) -> CarCatalogIndex | None:
        if not self.is_configured:
            return None
        ttl = max(self.settings.yandex_car_catalog_cache_ttl_seconds, 60)
        now = time.time()
        if not force_refresh and _CATALOG_CACHE["entries"] and now - float(_CATALOG_CACHE["loaded_at"]) < ttl:
            return _CATALOG_CACHE["entries"]  # type: ignore[return-value]

        try:
            payload = self.client.fetch_cars_catalog()
            entries = parse_catalog_payload(payload)
            index = build_catalog_index(entries)
            if index.size == 0 and not index.brands:
                logger.warning("Yandex cars catalog returned no brand/model entries")
                return None
            _CATALOG_CACHE["loaded_at"] = now
            _CATALOG_CACHE["entries"] = index
            logger.info("Loaded Yandex cars catalog: %s brands, %s models", len(index.brands), index.size)
            return index
        except Exception as exc:
            logger.warning("Failed to load Yandex cars catalog: %s", exc)
            cached = _CATALOG_CACHE.get("entries")
            if cached:
                return cached  # type: ignore[return-value]
            return None

    def resolve_brand(self, value: str, *, index: CarCatalogIndex | None = None) -> str | None:
        catalog = index or self.get_index()
        if catalog is None:
            return normalize_car_brand(value)
        for candidate in _brand_candidates(value):
            resolved = catalog.brands.get(catalog_match_key(candidate))
            if resolved:
                return resolved
        return None

    def resolve_model(self, brand: str, value: str, *, index: CarCatalogIndex | None = None) -> str | None:
        catalog = index or self.get_index()
        normalized_brand = normalize_car_brand(brand)
        if catalog is None:
            return normalize_car_model(value)
        brand_key = catalog_match_key(normalized_brand)
        canonical_brand = catalog.brands.get(brand_key)
        if not canonical_brand:
            return None
        brand_key = catalog_match_key(canonical_brand)
        for candidate in _model_candidates(value, canonical_brand):
            resolved = catalog.models.get((brand_key, catalog_match_key(candidate)))
            if resolved:
                return resolved
        return None

    def resolve_brand_model(self, value: str, *, index: CarCatalogIndex | None = None) -> tuple[str, str] | None:
        catalog = index or self.get_index()
        if catalog is None:
            brand = extract_known_car_brand(value) or normalize_car_brand(value)
            model = normalize_car_model(value)
            if brand and model:
                return brand, model
            return None

        known_brand = extract_known_car_brand(value)
        if known_brand:
            canonical_brand = self.resolve_brand(known_brand, index=catalog)
            if not canonical_brand:
                return None
            tail = _strip_brand_prefix(value, known_brand)
            canonical_model = self.resolve_model(canonical_brand, tail or value, index=catalog)
            if canonical_model:
                return canonical_brand, canonical_model

        normalized = normalize_text_token(value)
        for brand_key, canonical_brand in sorted(catalog.brands.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized == brand_key or normalized.startswith(f"{brand_key} "):
                tail = normalized[len(brand_key) :].strip()
                canonical_model = self.resolve_model(canonical_brand, tail or value, index=catalog)
                if canonical_model:
                    return canonical_brand, canonical_model
        return None

    def validate_pair(self, brand: str | None, model: str | None) -> tuple[str | None, str | None, list[str]]:
        if not brand or not model:
            return brand, model, []
        catalog = self.get_index()
        if catalog is None:
            return normalize_car_brand(brand), normalize_car_model(model), []

        canonical_brand = self.resolve_brand(brand, index=catalog)
        if not canonical_brand:
            return None, None, [f"invalid:car_brand_not_in_catalog:{brand}"]

        canonical_model = self.resolve_model(canonical_brand, model, index=catalog)
        if not canonical_model:
            return canonical_brand, None, [f"invalid:car_model_not_in_catalog:{brand}:{model}"]

        return canonical_brand, canonical_model, []


def get_yandex_car_catalog() -> YandexCarCatalog:
    return YandexCarCatalog()


def resolve_brand_input(value: str) -> tuple[str | None, list[str]]:
    catalog = get_yandex_car_catalog()
    if not catalog.is_configured:
        return normalize_car_brand(value), []
    resolved = catalog.resolve_brand(value)
    if resolved:
        return resolved, []
    return None, ["car_brand_not_in_catalog"]


def resolve_model_input(brand: str, value: str) -> tuple[str | None, list[str]]:
    catalog = get_yandex_car_catalog()
    if not catalog.is_configured:
        return normalize_car_model(value), []
    resolved = catalog.resolve_model(brand, value)
    if resolved:
        return resolved, []
    return None, ["car_model_not_in_catalog"]


def resolve_brand_model_input(value: str) -> tuple[str | None, str | None, list[str]]:
    catalog = get_yandex_car_catalog()
    if not catalog.is_configured:
        brand = extract_known_car_brand(value)
        model = normalize_car_model(value)
        if brand and model:
            return brand, model, []
        return None, None, ["invalid_vehicle_descriptor"]
    resolved = catalog.resolve_brand_model(value)
    if resolved:
        return resolved[0], resolved[1], []
    return None, None, ["car_brand_model_not_in_catalog"]


def catalog_validation_error_message(errors: list[str]) -> str:
    if any(error.startswith("invalid:car_brand_not_in_catalog") or error == "car_brand_not_in_catalog" for error in errors):
        return (
            "Эта марка не найдена в справочнике Яндекса. "
            "Напишите марку точно так, как в документах на авто. Например: Toyota, Kia, Hyundai."
        )
    if any(error.startswith("invalid:car_model_not_in_catalog") or error == "car_model_not_in_catalog" for error in errors):
        return (
            "Эта модель не найдена в справочнике Яндекса для указанной марки. "
            "Напишите название модели из документов, как Camry, Rio, S-Class или X5 — "
            "не код кузова (w221, e90 и т.п.)."
        )
    if "car_brand_model_not_in_catalog" in errors:
        return (
            "Марка и модель не найдены в справочнике Яндекса. "
            "Напишите их отдельно и точно, как в документах на авто."
        )
    return "Проверьте марку и модель автомобиля и отправьте значение еще раз."


def _brand_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    for item in (value, normalize_car_brand(value), extract_known_car_brand(value)):
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def _model_candidates(value: str, brand: str) -> list[str]:
    candidates: list[str] = []
    for item in (
        value,
        normalize_car_model(value),
        _strip_brand_prefix(value, brand),
        _strip_brand_prefix(normalize_car_model(value), brand),
    ):
        cleaned = (item or "").strip()
        if not cleaned:
            continue
        for variant in iter_car_model_normalize_candidates(cleaned, brand=brand):
            if variant not in candidates:
                candidates.append(variant)
    return candidates


def _strip_brand_prefix(value: str, brand: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    normalized = normalize_text_token(raw)
    brand_key = catalog_match_key(brand)
    value_key = catalog_match_key(raw)
    if value_key.startswith(brand_key):
        remainder = normalized[len(normalize_text_token(brand)) :].strip()
        return remainder or raw
    first_token = raw.split(maxsplit=1)[0]
    if catalog_match_key(first_token) == brand_key and " " in raw:
        return raw.split(maxsplit=1)[1].strip()
    return raw
