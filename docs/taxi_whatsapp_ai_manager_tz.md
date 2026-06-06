# Техническое задание  
# WhatsApp AI-менеджер для регистрации водителей в таксопарк через Yandex Fleet

## 1. Назначение проекта

Необходимо разработать микросервис **WhatsApp AI-менеджера** для таксопарка.

Система должна принимать сообщения от водителей в WhatsApp, вести диалог, собирать данные и документы для подключения к таксопарку, сохранять документы в Google Drive, отображать заявки и статусы в Google Sheets, а после сбора полной информации отправлять данные в **Yandex Fleet** для регистрации водителя в таксопарк.

Основной сценарий: **водитель сам первым пишет в WhatsApp**, после чего бот начинает процесс регистрации.

---

## 2. Общая схема работы

```text
Водитель пишет в WhatsApp
→ Meta WhatsApp Cloud API отправляет webhook в микросервис
→ микросервис определяет водителя по номеру телефона
→ AI-менеджер ведёт диалог
→ система собирает данные водителя
→ система собирает данные автомобиля
→ система принимает фото документов
→ документы сохраняются в Google Drive
→ заявка и статус сохраняются в PostgreSQL
→ данные отображаются в Google Sheets
→ микросервис отправляет данные в Yandex Fleet
→ статус регистрации обновляется
→ водитель получает ответ в WhatsApp
```

---

## 3. Что входит в проект

В рамках проекта необходимо реализовать:

1. Интеграцию с WhatsApp Cloud API.
2. Приём входящих сообщений через webhook.
3. Отправку сообщений водителю в WhatsApp.
4. AI-диалог с водителем.
5. State machine для пошагового сбора данных.
6. Сбор данных водителя.
7. Сбор данных автомобиля.
8. Приём фото документов.
9. Скачивание медиафайлов из WhatsApp.
10. Загрузку документов в Google Drive.
11. Хранение заявок в PostgreSQL.
12. Отображение заявок и статусов в Google Sheets.
13. Интеграцию с Yandex Fleet.
14. Отправку собранных данных в Yandex Fleet.
15. Обновление статусов заявки.
16. Обработку ошибок.
17. README по запуску и настройке.

---

## 4. Что не входит в проект

В проект не входит:

- отдельная CRM;
- отдельная админ-панель;
- личный кабинет администратора;
- мобильное приложение;
- голосовой бот;
- массовые рассылки;
- холодные исходящие сообщения;
- OCR-распознавание документов;
- ручная модерация документов внутри системы;
- сложная аналитика;
- роли менеджеров;
- биллинг;
- полноценная замена кабинета Yandex Fleet.

Google Sheets используется только для видимости администратору: заявки, данные, документы, статусы, ошибки.

---

## 5. Технологический стек

```text
Backend: Python FastAPI
Database: PostgreSQL
ORM: SQLAlchemy
Migrations: Alembic
AI: OpenAI API / Gemini API
WhatsApp: Meta WhatsApp Cloud API
Files: Google Drive API
Visibility: Google Sheets API
Yandex: Yandex Fleet API
Deploy: Docker + VPS
Reverse Proxy: Nginx
```

---

## 6. Основные модули системы

### 6.1. WhatsApp-модуль

Модуль отвечает за работу с Meta WhatsApp Cloud API.

Функции:

- верификация webhook от Meta;
- приём входящих сообщений;
- парсинг номера отправителя;
- парсинг текстовых сообщений;
- парсинг изображений и документов;
- получение `media_id`;
- скачивание медиафайлов;
- отправка текстовых сообщений в WhatsApp;
- обработка ошибок WhatsApp API.

Типы сообщений:

```text
text
image
document
unsupported
```

---

### 6.2. Dialogue Engine

Модуль отвечает за сценарий регистрации водителя.

Диалог должен строиться не только на AI, а на связке:

```text
State machine + AI
```

State machine контролирует обязательные шаги, AI помогает понимать свободный текст и формулировать ответы.

Статусы диалога:

```text
new
ask_full_name
ask_phone
ask_city
ask_iin
ask_has_car
ask_car_brand
ask_car_model
ask_car_year
ask_car_plate
ask_car_color
ask_driver_license_front
ask_driver_license_back
ask_id_card
ask_vehicle_registration_doc
ask_selfie_with_license
ask_rent_or_power_of_attorney
confirm_data
ready_to_send_yandex
sending_to_yandex
sent_to_yandex
yandex_error
completed
```

---

### 6.3. AI-модуль

AI используется для:

1. Ответов водителю в естественном стиле.
2. Извлечения данных из свободного текста.
3. Понимания намерений водителя.
4. Ответов на частые вопросы.
5. Обработки нестандартных сообщений.
6. Уточнений, если данные неполные или непонятные.

AI должен возвращать строго структурированный JSON.

Пример:

```json
{
  "reply": "Отлично, я записал город Астана. Теперь напишите марку автомобиля.",
  "intent": "registration",
  "extracted_fields": {
    "city": "Астана"
  },
  "next_state": "ask_car_brand",
  "confidence": 0.94
}
```

Если AI не уверен:

```json
{
  "reply": "Уточните, пожалуйста, марку автомобиля отдельным сообщением.",
  "intent": "clarification",
  "extracted_fields": {},
  "next_state": "ask_car_brand",
  "confidence": 0.42
}
```

---

## 7. Данные, которые должен собрать бот

### 7.1. Данные водителя

Система должна собрать:

```text
ФИО
Номер телефона
Город работы
ИИН
Дата рождения, если требуется Yandex Fleet
```

---

### 7.2. Данные автомобиля

Если водитель работает на своём автомобиле, система должна собрать:

```text
Наличие личного автомобиля
Марка автомобиля
Модель автомобиля
Год выпуска
Госномер
Цвет
VIN, если требуется Yandex Fleet
```

---

### 7.3. Документы

Система должна запросить и принять:

```text
Фото водительского удостоверения — лицевая сторона
Фото водительского удостоверения — обратная сторона
Фото удостоверения личности
Фото СТС / техпаспорта автомобиля
Селфи с водительским удостоверением
Доверенность или договор аренды, если автомобиль не принадлежит водителю
Дополнительные фото автомобиля, если требует Yandex Fleet / таксопарк
```

Финальный список обязательных документов уточняется после получения требований таксопарка и доступа к Yandex Fleet.

---

## 8. Работа с документами

Когда водитель отправляет фото или документ:

1. WhatsApp webhook получает сообщение с `media_id`.
2. Микросервис скачивает файл через WhatsApp Cloud API.
3. Файл загружается в Google Drive.
4. В PostgreSQL сохраняется информация о документе:
   - тип документа;
   - ссылка на файл;
   - Google Drive file ID;
   - WhatsApp media ID;
   - дата загрузки;
   - статус.
5. Ссылка на документ отображается в Google Sheets.
6. Бот запрашивает следующий документ.

Структура папок в Google Drive:

```text
TaxiPark Drivers/
  2026/
    06/
      +77071234567_Иван_Иванов/
        driver_license_front.jpg
        driver_license_back.jpg
        id_card.jpg
        vehicle_doc.jpg
        selfie_with_license.jpg
        rent_or_power_of_attorney.jpg
```

---

## 9. Google Sheets

Google Sheets используется для видимости администратору.

Таблица должна содержать следующие колонки:

```text
Дата заявки
WhatsApp номер
ФИО
Телефон
Город
ИИН
Марка авто
Модель авто
Год авто
Госномер
Цвет
Права лицевая сторона
Права обратная сторона
Удостоверение личности
Техпаспорт / СТС
Селфи с правами
Доверенность / аренда
Статус заявки
Статус Yandex Fleet
Yandex Driver ID
Yandex Vehicle ID
Ошибка Yandex
Дата последнего сообщения
Дата отправки в Yandex
```

Возможные статусы заявки:

```text
collecting_data
waiting_documents
confirming_data
ready_to_send_yandex
sending_to_yandex
sent_to_yandex
yandex_error
completed
```

---

## 10. PostgreSQL

PostgreSQL является основным хранилищем системы.  
Google Sheets не является основной базой, а используется только для отображения.

### 10.1. Таблица `drivers`

```text
id
whatsapp_phone
full_name
phone
city
iin
birth_date
state
created_at
updated_at
last_message_at
```

### 10.2. Таблица `vehicles`

```text
id
driver_id
brand
model
year
plate_number
color
vin
created_at
updated_at
```

### 10.3. Таблица `documents`

```text
id
driver_id
document_type
file_url
google_drive_file_id
whatsapp_media_id
status
created_at
```

### 10.4. Таблица `applications`

```text
id
driver_id
status
yandex_status
yandex_driver_id
yandex_vehicle_id
yandex_error
sent_to_yandex_at
created_at
updated_at
```

### 10.5. Таблица `messages`

```text
id
driver_id
direction
message_type
text
raw_payload
created_at
```

---

## 11. Интеграция с Yandex Fleet

Интеграция с Yandex Fleet должна быть вынесена в отдельный модуль.

Структура:

```text
integrations/yandex/
  client.py
  schemas.py
  mapper.py
  service.py
```

### 11.1. Что должен делать Yandex-модуль

После полной заявки система должна:

1. Проверить обязательные поля.
2. Собрать payload по водителю.
3. Собрать payload по автомобилю.
4. Передать данные в Yandex Fleet.
5. Получить ответ от Yandex Fleet.
6. Сохранить ID водителя, если возвращается.
7. Сохранить ID автомобиля, если возвращается.
8. Обновить статус заявки в PostgreSQL.
9. Обновить строку в Google Sheets.
10. Отправить водителю сообщение в WhatsApp.

---

### 11.2. Доступы Yandex Fleet

От заказчика нужны:

```text
Park ID
Client ID
API Key
доступная документация / раздел API
требования к обязательным полям
права API-ключа
```

Интеграция реализуется по фактически доступным методам Yandex Fleet.

Если API позволяет создать водителя и автомобиль — система выполняет автоматическую регистрацию.

Если API позволяет выполнить только часть действий — система реализует доступную часть.

Если API возвращает ошибку или не даёт создать водителя — система сохраняет заявку и ошибку в PostgreSQL и Google Sheets.

---

## 12. Отправка заявки в Yandex Fleet

Когда заявка получает статус:

```text
ready_to_send_yandex
```

микросервис выполняет:

```text
validate application
→ build yandex payload
→ send driver data
→ send vehicle data
→ attach vehicle to driver, if required
→ save yandex response
→ update PostgreSQL
→ update Google Sheets
→ notify driver in WhatsApp
```

Пример внутреннего payload:

```json
{
  "driver": {
    "full_name": "Иван Иванов",
    "phone": "+77071234567",
    "city": "Астана",
    "iin": "000000000000"
  },
  "vehicle": {
    "brand": "Toyota",
    "model": "Camry",
    "year": 2018,
    "plate_number": "123ABC01",
    "color": "white"
  },
  "documents": {
    "driver_license_front": "https://drive.google.com/...",
    "driver_license_back": "https://drive.google.com/...",
    "id_card": "https://drive.google.com/...",
    "vehicle_doc": "https://drive.google.com/...",
    "selfie_with_license": "https://drive.google.com/..."
  }
}
```

---

## 13. Ответы водителю

### 13.1. Начало регистрации

```text
Здравствуйте! Я помогу вам подключиться к таксопарку. 
Для начала напишите ваше ФИО.
```

### 13.2. Сбор города

```text
Укажите город, в котором планируете работать.
```

### 13.3. Сбор ИИН

```text
Напишите ваш ИИН. Он нужен для регистрации в системе таксопарка.
```

### 13.4. Сбор данных автомобиля

```text
У вас есть личный автомобиль для работы? Ответьте: да или нет.
```

```text
Напишите марку автомобиля, например Toyota.
```

```text
Теперь напишите модель автомобиля, например Camry.
```

```text
Укажите год выпуска автомобиля.
```

```text
Укажите госномер автомобиля.
```

```text
Укажите цвет автомобиля.
```

### 13.5. Сбор документов

```text
Теперь отправьте фото водительского удостоверения: лицевую сторону.
```

```text
Принял. Теперь отправьте фото водительского удостоверения: обратную сторону.
```

```text
Принял. Теперь отправьте фото удостоверения личности.
```

```text
Принял. Теперь отправьте фото техпаспорта / СТС автомобиля.
```

```text
Принял. Теперь отправьте селфи с водительским удостоверением.
```

### 13.6. Подтверждение данных

```text
Проверьте данные:

ФИО: Иван Иванов
Город: Астана
ИИН: 000000000000
Авто: Toyota Camry 2018
Госномер: 123ABC01
Цвет: белый

Если всё верно, напишите "Подтверждаю".
Если нужно исправить, напишите что изменить.
```

### 13.7. Отправка в Yandex Fleet

```text
Спасибо, данные собраны. Отправляю заявку на регистрацию в таксопарк.
```

### 13.8. Успех

```text
Готово! Ваша заявка отправлена в систему таксопарка. 
Ожидайте дальнейшие инструкции по выходу на линию.
```

### 13.9. Ошибка Yandex Fleet

```text
Не удалось автоматически отправить заявку. 
Данные сохранены, ошибка передана в систему. Попробуем обработать заявку повторно.
```

---

## 14. FAQ / база знаний

Бот должен уметь отвечать на частые вопросы:

```text
Какие документы нужны?
Как скачать Яндекс Про?
Как подключиться к таксопарку?
Сколько занимает регистрация?
Можно ли работать без своего авто?
Какие авто подходят?
Что делать, если не приходит SMS?
Что делать, если документы не проходят?
Как выйти на линию?
Как узнать статус заявки?
```

FAQ хранится в Markdown/JSON-файлах:

```text
knowledge_base/
  registration.md
  documents.md
  yandex_pro.md
  car_requirements.md
  common_errors.md
```

AI должен отвечать только по базе знаний и данным таксопарка. Если ответа нет, бот должен попросить уточнение или сообщить, что вопрос требует проверки.

---

## 15. Обработка ошибок

Система должна обрабатывать следующие случаи:

```text
водитель отправил не тот документ
водитель отправил текст вместо фото
водитель отправил фото раньше времени
водитель пропустил обязательное поле
водитель написал непонятное сообщение
WhatsApp API не отвечает
Google Drive API не отвечает
Google Sheets API не отвечает
Yandex Fleet вернул ошибку
AI вернул невалидный JSON
```

Fallback-ответ при непонятном сообщении:

```text
Не совсем понял сообщение. Пожалуйста, ответьте ещё раз коротко.
```

Fallback-ответ при ошибке AI:

```text
Произошла техническая ошибка при обработке сообщения. Попробуйте отправить сообщение ещё раз.
```

Fallback-ответ при ошибке Yandex Fleet:

```text
Не удалось отправить данные в систему таксопарка. Данные сохранены, попробуем обработать заявку повторно.
```

---

## 16. Безопасность

Необходимо:

- хранить токены только в `.env`;
- не хранить секреты в коде;
- не отправлять документы в AI без необходимости;
- не логировать содержимое документов;
- валидировать webhook от Meta;
- ограничить доступ к Google Drive папкам;
- сохранять raw payload WhatsApp для отладки;
- логировать ошибки интеграций;
- использовать HTTPS для webhook;
- использовать отдельный сервисный Google-аккаунт.

---

## 17. ENV-переменные

```env
APP_ENV=production
APP_HOST=https://domain.kz

DATABASE_URL=postgresql://user:password@db:5432/taxibot

OPENAI_API_KEY=
GEMINI_API_KEY=

WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_VERIFY_TOKEN=

GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_SHEETS_ID=

YANDEX_PARK_ID=
YANDEX_CLIENT_ID=
YANDEX_API_KEY=
YANDEX_API_BASE_URL=
```

---

## 18. API микросервиса

### 18.1. WhatsApp webhook verification

```http
GET /webhooks/whatsapp
```

Используется Meta для проверки webhook.

### 18.2. WhatsApp incoming messages

```http
POST /webhooks/whatsapp
```

Используется для приёма входящих сообщений от WhatsApp.

### 18.3. Healthcheck

```http
GET /health
```

Проверка состояния сервиса.

### 18.4. Получение заявки

```http
GET /applications/{id}
```

Опциональный endpoint для отладки.

---

## 19. Структура проекта

```text
taxi-ai-manager/
  app/
    main.py
    config.py

    whatsapp/
      webhook.py
      parser.py
      sender.py
      media.py

    dialog/
      states.py
      engine.py
      prompts.py
      ai.py
      faq.py

    drivers/
      models.py
      schemas.py
      service.py

    vehicles/
      models.py
      service.py

    documents/
      models.py
      service.py

    applications/
      models.py
      service.py

    integrations/
      google_drive.py
      google_sheets.py
      yandex/
        client.py
        mapper.py
        schemas.py
        service.py

    database/
      session.py
      base.py

    utils/
      logger.py
      validators.py

  alembic/
  docker-compose.yml
  Dockerfile
  .env.example
  README.md
```

---

## 20. Основная логика обработки сообщения

```text
1. Получить webhook от WhatsApp
2. Распарсить номер, текст, тип сообщения
3. Найти или создать driver по whatsapp_phone
4. Загрузить текущее состояние заявки
5. Если сообщение текстовое:
   - отправить в dialogue engine
   - извлечь поля
   - обновить БД
   - определить следующий статус
6. Если сообщение содержит фото:
   - скачать media из WhatsApp
   - загрузить файл в Google Drive
   - сохранить ссылку в БД
   - определить следующий статус
7. Обновить Google Sheets
8. Если все данные собраны:
   - показать водителю подтверждение
9. Если водитель подтвердил:
   - отправить данные в Yandex Fleet
10. Обновить статус заявки
11. Отправить ответ в WhatsApp
```

---

## 21. Acceptance Criteria

Проект считается готовым, если:

1. Водитель может написать в WhatsApp и получить ответ.
2. Бот корректно начинает регистрацию.
3. Бот собирает ФИО, телефон, город и ИИН.
4. Бот собирает данные автомобиля.
5. Бот принимает фото документов.
6. Документы сохраняются в Google Drive.
7. Заявка появляется в Google Sheets.
8. Статусы заявки обновляются в Google Sheets.
9. После подтверждения заявка отправляется в Yandex Fleet.
10. Если Yandex Fleet возвращает ошибку, ошибка сохраняется в PostgreSQL и Google Sheets.
11. Водитель получает понятный ответ по результату.
12. Система работает после деплоя на сервере.
13. Все токены вынесены в `.env`.
14. Есть README по запуску и настройке.

---

## 22. Что нужно от заказчика

Для старта работ заказчик должен предоставить:

### 22.1. Meta / WhatsApp

```text
Meta Business доступ
Отдельный номер WhatsApp для бота
WhatsApp Cloud API access token
Phone Number ID
WhatsApp Business Account ID
Verify token
```

### 22.2. Google

```text
Google Drive папка для документов
Google Sheets таблица для заявок
Google service account credentials
Доступ service account к Drive и Sheets
```

### 22.3. Yandex Fleet

```text
Park ID
Client ID
API Key
Yandex Fleet API Base URL
Документация или доступ к разделу API
Список обязательных полей для регистрации
Список обязательных документов
```

### 22.4. Бизнес-логика таксопарка

```text
Название таксопарка
Города работы
Какие автомобили подходят
Какие документы обязательны
Нужен ли ИИН
Нужна ли дата рождения
Нужен ли договор аренды / доверенность
Тексты приветствия и финальных сообщений
FAQ по подключению
```

---

## 23. Итоговая архитектура

```text
WhatsApp Cloud API
→ FastAPI microservice
→ PostgreSQL
→ AI Dialogue Engine
→ Google Drive
→ Google Sheets
→ Yandex Fleet
```

Без отдельной CRM.  
Без отдельной админ-панели.  
Google Sheets — для видимости администратора.  
Google Drive — для хранения документов.  
Yandex Fleet — основной endpoint для регистрации.  
AI — для общения и извлечения данных.  
State machine — для контроля сценария регистрации.
