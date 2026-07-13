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
        "driver_license": "водительское удостоверение",
        "id_card": "удостоверение личности",
        "vehicle_registration_doc": "техпаспорт / СТС",
        "selfie_with_license": "селфи с ВУ",
    }

    DOCUMENTS_ORDER = (
        "driver_license",
        "vehicle_registration_doc",
    )

    NEXT_STEP_PROMPTS = {
        "driver_license": "Пришлите фото или PDF водительского удостоверения.",
        "vehicle_registration_doc": "Пришлите фото или PDF техпаспорта / СТС.",
        "selfie_with_license": "Пришлите селфи с водительским удостоверением.",
        "full_name": "Напишите ФИО полностью.",
        "iin": "Напишите ИИН (12 цифр).",
        "birth_date": "Напишите дату рождения в формате ДД.ММ.ГГГГ.",
        "city": "Напишите город.",
        "address": "Напишите адрес.",
        "driver_license_number": "Напишите номер водительского удостоверения.",
        "driver_license_issue_date": "Напишите дату выдачи ВУ.",
        "driver_license_expires_at": "Напишите срок действия ВУ.",
        "driving_experience_since": "Напишите дату начала стажа.",
        "brand": "Напишите марку автомобиля, например Toyota.",
        "model": "Напишите модель автомобиля, например Camry.",
        "year": "Напишите год выпуска автомобиля.",
        "plate_number": "Напишите госномер автомобиля.",
        "color": "Напишите цвет автомобиля.",
        "registration_certificate": "Напишите номер СТС.",
        "phone": "Напишите контактный телефон.",
    }

    def document_progress(self, draft: dict) -> tuple[int, int, list[str]]:
        documents = draft.get("documents") or {}
        received = [key for key in self.DOCUMENTS_ORDER if documents.get(key)]
        missing = [key for key in self.DOCUMENTS_ORDER if not documents.get(key)]
        return len(received), len(self.DOCUMENTS_ORDER), missing

    def next_step_text(self, draft: dict, missing_fields: list[str] | None = None) -> str:
        _, _, missing_docs = self.document_progress(draft)
        if missing_docs:
            first_doc = missing_docs[0]
            return self.NEXT_STEP_PROMPTS.get(first_doc, f"Пришлите: {self._doc_label(first_doc)}.")
        fields = missing_fields if missing_fields is not None else list(draft.get("missing_fields") or [])
        actionable = [field for field in fields if field not in self.DOCUMENTS_ORDER]
        if actionable:
            field = actionable[0]
            return self.NEXT_STEP_PROMPTS.get(field, f"Укажите: {self.LABELS.get(field, field)}.")
        return "Проверьте данные и нажмите «Подтверждаю»."

    def build_document_reply(self, document_type: str, extracted_fields: dict[str, str], missing_fields: list[str], draft: dict | None = None) -> str:
        draft = draft or {"documents": {}, "missing_fields": missing_fields}
        received, total, _ = self.document_progress(draft)
        lines = [
            f"Документ получил: {self._doc_label(document_type)}.",
            f"Документы: {received} из {total}.",
            "Распознал:",
        ]
        lines.extend(self._render_fields(extracted_fields))
        lines.append("")
        lines.append(f"Следующий шаг: {self.next_step_text(draft, missing_fields)}")
        return "\n".join(lines)

    def build_final_summary(self, draft: dict) -> str:
        driver = draft.get("driver", {})
        vehicle = draft.get("vehicle", {})
        documents = draft.get("documents", {})
        dash = "—"
        received, total, _ = self.document_progress(draft)
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
                f"Документы: {received} из {total}",
            ]
        )
        for key in self.DOCUMENTS_ORDER:
            status = "есть" if documents.get(key) else "нет"
            lines.append(f"- {self._doc_label(key)}: {status}")
        for key in ("selfie_with_license", "id_card"):
            if documents.get(key):
                lines.append(f"- {self._doc_label(key)}: есть")
        if draft.get("ready_for_yandex") or draft.get("is_registration_complete"):
            lines.append("")
            lines.append("Статус: анкета готова к отправке в Яндекс.")
        lines.append("")
        lines.append("Если всё верно — нажмите «Подтверждаю».")
        lines.append("Если нужно исправить — нажмите «Исправить».")
        return "\n".join(lines)

    def build_missing_text(self, missing_fields: list[str], draft: dict | None = None) -> str:
        if not missing_fields:
            return "Ничего не пропущено. Можно подтверждать анкету."
        draft = draft or {"documents": {}, "missing_fields": missing_fields}
        received, total, _ = self.document_progress(draft)
        next_step = self.next_step_text(draft, missing_fields)
        lines = [
            f"Документы: {received} из {total}.",
            f"Следующий шаг: {next_step}",
        ]
        # Show other missing items beyond the immediate next step.
        _, _, missing_docs = self.document_progress(draft)
        actionable = [field for field in missing_fields if field not in self.DOCUMENTS_ORDER]
        primary = missing_docs[0] if missing_docs else (actionable[0] if actionable else None)
        remaining = [field for field in missing_fields if field != primary][:5]
        if remaining:
            lines.append("")
            lines.append("Потом ещё понадобится:")
            lines.extend(self._render_missing(remaining))
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
        return self.LABELS.get(
            document_type,
            {
                "unknown": "неизвестный документ",
            }.get(document_type, document_type),
        )
