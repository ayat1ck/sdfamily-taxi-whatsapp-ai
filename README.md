# Taxi WhatsApp AI Manager

Микросервис на FastAPI для регистрации водителей таксопарка через WhatsApp. Сервис принимает webhook от WhatsApp Cloud API, ведет пошаговый диалог, сохраняет заявку в PostgreSQL и отправляет данные в Google Drive, Google Sheets и Yandex Fleet.

## Что реализовано

- `GET /health` для healthcheck.
- `GET /webhooks/whatsapp` для верификации webhook Meta.
- `POST /webhooks/whatsapp` для приема входящих сообщений.
- State machine регистрации водителя и автомобиля.
- Обработка текстовых сообщений, фото и документов.
- SQLAlchemy-модели: `drivers`, `vehicles`, `documents`, `applications`, `messages`.
- Реальные интеграционные клиенты для WhatsApp Cloud API, Google Drive, Google Sheets и Yandex Fleet.
- База знаний в `knowledge_base/`.
- Dockerfile, `docker-compose.yml`, `.env.example`.
- Профиль водителя и анкета Yandex приведены ближе к форме `Новый профиль` в парке: тип исполнителя, адрес, стаж, ВУ, дата принятия, условие работы, существующий или новый автомобиль.

## Структура

```text
app/
  applications/
  database/
  dialog/
  documents/
  drivers/
  integrations/
  messages/
  utils/
  vehicles/
  whatsapp/
knowledge_base/
```

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

- `DATABASE_URL`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `GOOGLE_DRIVE_FOLDER_ID`
- `GOOGLE_SHEETS_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON` или `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64`
- `YANDEX_PARK_ID`
- `YANDEX_CLIENT_ID`
- `YANDEX_API_KEY`
- `YANDEX_DRIVER_PROFILE_WORK_RULE_ID`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## AI-слой

- System prompt: `app/dialog/llm_prompt.py`
- Structured response schema: `app/dialog/ai.py`
- AI orchestration and fallback: `app/dialog/ai.py`
- State machine: `app/dialog/states.py`
- Следующий вопрос и сценарные тексты: `app/dialog/prompts.py`

Если `AI_PROVIDER=openai` и заполнен `OPENAI_API_KEY`, сервис использует OpenAI Responses API со structured output.
Если ключа нет, сервис автоматически откатывается на deterministic fallback, чтобы не ломать регистрацию.

## Локальный запуск

### Вариант 1. Python

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Вариант 2. Docker Compose

```bash
docker compose up --build
```

Сервис будет доступен по адресу [http://localhost:8000](http://localhost:8000).

## Пример webhook verification

```http
GET /webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=changeme&hub.challenge=12345
```

Ответ:

```text
12345
```

## Пример payload входящего текста

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Иван Иванов"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## Ограничения текущей версии

- Без `OPENAI_API_KEY` сервис работает в fallback-режиме без реального LLM-вызова.
- В проект добавлен минимальный `alembic` контур и стартовая миграция, но приложение по-прежнему создает таблицы на старте через `Base.metadata.create_all()` для упрощенного локального запуска.
- По Yandex Fleet состав полей зависит от конкретной схемы парка. В `.env` вынесены поля, которые обычно требуются для создания профиля водителя и автомобиля, но если парк требует дополнительные атрибуты, их придется добавить в маппинг.
