from __future__ import annotations


class SummaryBuilder:
    LABELS = {
        "full_name": "ФИО",
        "iin": "ИИН",
        "birth_date": "Дата рождения",
        "phone": "Телефон",
        "city": "Город",
        "address": "Адрес",
        "driving_experience_since": "Стаж",
        "driver_license_number": "ВУ",
        "driver_license_issue_date": "Дата выдачи ВУ",
        "driver_license_expires_at": "ВУ действует до",
        "brand": "Авто",
        "model": "Модель",
        "year": "Год",
        "plate_number": "Госномер",
        "registration_certificate": "СТС",
        "color": "Цвет",
        "vin": "VIN",
    }

    def build_document_reply(self, document_type: str, extracted_fields: dict[str, str], missing_fields: list[str]) -> str:
        lines = [f"Документ получил: {self._doc_label(document_type)}.", "Распознал:"]
        lines.extend(self._render_fields(extracted_fields))
        lines.append("Ещё нужно:")
        lines.extend(self._render_missing(missing_fields))
        return "\n".join(lines)

    def build_final_summary(self, draft: dict) -> str:
        driver = draft.get("driver", {})
        vehicle = draft.get("vehicle", {})
        documents = draft.get("documents", {})
        dash = "—"
        lines = [
            "Проверьте данные:",
            f"ФИО: {driver.get('full_name') or dash}",
            f"ИИН: {driver.get('iin') or dash}",
            f"Дата рождения: {driver.get('birth_date') or dash}",
            f"Телефон: {driver.get('phone') or dash}",
            f"Город: {driver.get('city') or dash}",
        ]
        if driver.get("address"):
            lines.append(f"Адрес: {driver.get('address')}")
        lines.extend(
            [
                f"Стаж: {driver.get('driving_experience_since') or dash}",
                f"ВУ: {driver.get('driver_license_number') or dash}",
                f"Авто: {vehicle.get('brand') or dash} {vehicle.get('model') or ''}".strip(),
                f"Госномер: {vehicle.get('plate_number') or dash}",
                f"СТС: {vehicle.get('registration_certificate') or dash}",
                f"Цвет: {vehicle.get('color') or dash}",
                "Документы:",
            ]
        )
        for key in ("driver_license", "vehicle_registration_doc"):
            status = "есть" if documents.get(key) else "нет"
            lines.append(f"- {self._doc_label(key)}: {status}")
        for key in ("id_card", "selfie_with_license"):
            if documents.get(key):
                lines.append(f"- {self._doc_label(key)}: есть")
        if draft.get("ready_for_yandex") or draft.get("is_registration_complete"):
            lines.append("")
            lines.append("Статус: анкета готова к отправке в Яндекс.")
        lines.append("")
        lines.append('Если всё верно, напишите "Подтверждаю".')
        lines.append("Если нужно исправить - напишите, что изменить.")
        return "\n".join(lines)

    def build_missing_text(self, missing_fields: list[str]) -> str:
        if not missing_fields:
            return "Ничего не пропущено."
        lines = ["Ещё нужно:"]
        lines.extend(self._render_missing(missing_fields))
        return "\n".join(lines)

    def _render_fields(self, fields: dict[str, str]) -> list[str]:
        if not fields:
            return ["- ничего уверенного не распознал"]
        return [f"- {self.LABELS.get(key, key)}: {value}" for key, value in fields.items()]

    def _render_missing(self, missing_fields: list[str]) -> list[str]:
        if not missing_fields:
            return ["- ничего"]
        return [f"- {self.LABELS.get(field, field)}" for field in missing_fields]

    def _doc_label(self, document_type: str) -> str:
        return {
            "driver_license": "ВУ",
            "id_card": "удостоверение личности",
            "vehicle_registration_doc": "техпаспорт / СТС",
            "selfie_with_license": "селфи с ВУ",
            "unknown": "неизвестный документ",
        }.get(document_type, document_type)
