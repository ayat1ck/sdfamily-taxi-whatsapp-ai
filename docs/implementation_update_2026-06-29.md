# Implementation Update — 2026-06-29

Этот файл описывает, что было реализовано в коде по плану стабилизации, конверсии и автономности SD Family Taxi Bot на текущем этапе.

## Что было сделано

Реализация в этом заходе закрывает в первую очередь фундамент:

1. стабилизация routing и registration flow
2. понятная обработка OCR-провалов
3. автоматическое выявление застрявших диалогов
4. базовая наблюдаемость для unknown intents и reminders

Сделано без смены архитектуры:

- `DialogueEngine` остался главным оркестратором
- deterministic routing остался главным слоем
- LLM не поставлен во главу маршрутизации
- state machine регистрации не переписана с нуля

## Изменения по файлам

### 1. `app/dialog/engine.py`

Это главный файл, куда внесена основная логика.

Что добавлено:

- hard reset устаревшего `support_context_json`
- обновление `last_updated` у support context
- OCR failure counter через `support_context_json["consecutive_ocr_failures"]`
- автоматическое включение manual data entry после 2 подряд пустых OCR
- fallback normalization
- escalation в `requires_attention=True`, если водитель застрял 3 раза подряд
- запись unknown/fallback кейсов в таблицу `unknown_intents`
- отдельные registration debug traces для:
  - `media_during_text_step`
  - `text_during_doc_step`
  - `ocr_empty_result`
  - `ocr_manual_mode_enabled`

Что поменялось в поведении:

- если бот ждёт текст, а приходит фото:
  - OCR не запускается
  - бот отвечает человечески, что сейчас нужен текст
  - текущий вопрос повторяется

- если бот ждёт документ, а приходит текст:
  - бот не молчит
  - бот явно говорит, что сейчас нужен документ
  - текущий шаг повторяется

- если OCR не смог извлечь ни тип документа, ни поля:
  - это теперь отдельный сценарий, а не просто беззвучный провал
  - счётчик OCR ошибок увеличивается
  - после второй подряд неудачи включается manual mode

- если бот несколько раз подряд не понимает водителя:
  - `fallback_count` растёт
  - после 3 подряд случаев водитель помечается как требующий внимания
  - диалог уходит в human-needed/manual context

Дополнительные helper-методы, которые были добавлены:

- `_support_context_is_stale`
- `_reset_stale_support_context`
- `_touch_support_context`
- `_ocr_failure_count`
- `_increment_ocr_failure_counter`
- `_reset_ocr_failure_counter`
- `_set_manual_data_entry_enabled`
- `_reset_fallback_count`
- `_register_fallback`
- `_mark_successful_progress`

## 2. `app/documents/extraction.py`

Тут переработан контракт OCR extraction.

### Что было

Раньше extraction в случае проблемы фактически возвращал просто пустой результат. Снаружи было трудно понять:

- OCR технически упал
- OCR ответил пусто
- Gemini не сработал
- тип документа не найден

### Что стало

В `DocumentExtractionResult` добавлены поля:

- `provider_name`
- `provider_status`
- `provider_chain`
- `failure_reason`

Теперь extraction умеет различать:

- `disabled`
- `empty`
- `provider_error`
- `success`
- `fallback_success`

### OCR chain

Сделан provider chain:

1. сначала Gemini
2. если Gemini дал пустой результат или упал, пробуется OpenAI Vision
3. если оба не дали результата, движок дальше ведёт manual fallback

Это закрывает проблему single point of failure по OCR.

## 3. `app/dialog/faq.py`

Добавлены несколько быстрых deterministic-правил для более частых сценариев.

Уточнены кейсы для:

- `условия`
- `какие условия`
- `комиссия`
- `тариф`
- `выплата`
- `вход`
- `вход в яндекс`
- `яндекс про`
- `тіркелу`
- `тіркеу`
- `тиркейелин дегем`
- `не вижу парк`

Это не финальный routing hardening, но уже снижает количество тупых промахов на старте.

## 4. `app/unknown_intents/models.py`

Добавлена новая модель:

- `UnknownIntent`

Поля:

- `id`
- `driver_id`
- `message_id`
- `state_before`
- `message_text`
- `normalized_text`
- `message_type`
- `reason`
- `created_at`

Назначение:

- хранить реальные непонятные сообщения
- потом использовать как материал для расширения routing и словарей

## 5. `app/unknown_intents/service.py`

Добавлены сервисные методы:

- `create_unknown_intent(...)`
- `list_unknown_intents(...)`

Они используются из `engine.py` и admin API.

## 6. `alembic/versions/20260629_000004_unknown_intents.py`

Добавлена migration для таблицы `unknown_intents`.

Без этой миграции production-база не узнает о новой таблице.

## 7. `app/database/bootstrap.py`

Добавил `UnknownIntent.__table__` в runtime schema bootstrap.

Это полезно для локального/мягкого запуска, где схема может подхватываться без полной ручной миграции, но migration всё равно нужна как основной путь.

## 8. `app/admin/router.py`

Добавлен admin endpoint:

- `/admin/api/unknown-intents`

Он отдаёт последние unknown intents в JSON.

Поддерживает фильтр по `state`.

## 9. `app/admin/service.py`

Добавлены:

- `list_unknown_intents(...)`
- дополнительные dashboard counters

Сейчас в сервисе для dashboard появились метрики:

- `active_registrations`
- `awaiting_manager`
- `fallback_ge_3`
- `stale_support_contexts`
- `ocr_failures_24h`

Важно:

HTML-шаблоны админки я специально не стал глубоко ковырять в этом проходе, потому что там легко устроить лишний churn из-за кодировок. Но backend-данные для расширения наблюдаемости уже подготовлены.

## 10. `scripts/run_reminders.py`

Добавлен отдельный cron-friendly runner без Celery.

Он делает две вещи:

### registration reminder

Если водитель завис в registration state больше 4 часов:

- отправляется напоминание
- в reminder кладётся текущий шаг
- не шлётся спам по одному и тому же шагу повторно без контроля

### Yandex follow-up reminder

Если водитель завис в:

- `ASK_YANDEX_PRO_LOGIN`
- `ASK_YANDEX_PRO_PROBLEM_DETAILS`

дольше 2 часов:

- бот напоминает
- даёт CTA написать менеджеру

### escalation after reminders

Если reminder count доходит до 2:

- ставится `requires_attention=True`
- создаётся manager-visible event

## 11. `render.yaml`

Добавлен cron service:

- `sdfamily-taxi-reminders`

Он запускает:

- `python scripts/run_reminders.py`

по расписанию:

- `*/30 * * * *`

То есть каждые 30 минут.

## 12. `tests/test_routing.py`

Добавлен routing safety net.

Что покрыто:

- `NEW` state:
  - `1`
  - `регистрация`
  - `тіркелу`
  - `хочу подключиться`
  - `привет`
  - `условия`
  - `вход`
  - `я уже зареган`
  - и другие реальные кейсы

- active registration:
  - `Астана`
  - `пр. Республики 12`
  - `что делать`
  - `?`
  - `менеджер`

- existing driver menu mapping

Смысл теста:

- не дать сломать routing незаметно при следующих правках

## 13. `tests/test_support_context_ttl.py`

Добавлены тесты на TTL логики support context:

- support context жив через 2 часа
- support context сбрасывается после 24+ часов
- registration state без support context не ломается

## 14. `tests/test_ocr_fallback.py`

Добавлены тесты на OCR fallback:

- первый пустой OCR увеличивает счётчик
- второй пустой OCR может включить manual mode
- успешный OCR сбрасывает счётчик
- `DocumentExtractionResult` умеет представлять пустой outcome явно

## Что проверено

Проверялось:

### 1. Компиляция Python

Через `py_compile`:

- `app/dialog/engine.py`
- `app/documents/extraction.py`
- `app/dialog/faq.py`
- `app/admin/router.py`
- `app/admin/service.py`
- `app/unknown_intents/models.py`
- `app/unknown_intents/service.py`
- `scripts/run_reminders.py`

Ошибок синтаксиса после правок нет.

### 2. Тесты

Прогнаны:

- `tests.test_routing`
- `tests.test_support_context_ttl`
- `tests.test_ocr_fallback`
- `tests.test_stateful_support_menu`

Результат:

- тесты проходят
- часть старых тестов в минимальном runtime корректно skipped

## Что это решает practically

После этих изменений бот стал заметно менее глухим в типовых сценариях:

### Регистрация

- если пришло фото не в тот шаг, бот больше не должен тупо ломать flow
- если пришёл текст вместо документа, бот не должен молчать
- если OCR пустой два раза подряд, водитель не висит в пустоте, а переводится на ручный путь

### Застревания

- если бот несколько раз подряд не понял водителя, это теперь фиксируется явно
- водитель попадает в `requires_attention`
- это больше не “тихая смерть чата”

### OCR

- теперь есть fallback-провайдер
- можно отличить техническую ошибку OCR от пустого извлечения

### Диагностика

- unknown intents начинают накапливаться в отдельной таблице
- reminders вынесены в отдельный cron runner
- support context теперь живёт по TTL, а не вечно

## Что ещё не закрыто полностью

Хотя сделан большой кусок, план закрыт не на 100%.

Осталось добить следующие вещи.

### 1. Post-Yandex сценарий

Нужно ещё сильнее зачистить ветку после отправки в Яндекс:

- единый success path
- единый safe error path
- меньше технических хвостов в ответах водителю

Часть этого уже есть, но не доведена до идеала.

### 2. Полная observability в HTML админке

Backend-данные уже подготовлены, но сам UI админки ещё можно расширить:

- красивый вывод `support_context_json`
- последние 3 registration debug traces
- последние 5 сообщений отдельным compact-блоком

Я специально не делал агрессивную правку шаблонов в этом проходе, чтобы не устроить кодировочный развал.

### 3. LLM classification only for `NEW`

По плану это Phase 3:

- узкая LLM-классификация только для `NEW`
- только когда deterministic routing не уверен
- без права LLM менять state напрямую

Это ещё не добито.

### 4. Дополнительные routing cases

Текущий `tests/test_routing.py` уже полезен, но его надо дальше расширять живыми фразами из чатов.

Особенно:

- казахские варианты
- опечатки
- existing-driver slang
- смешанные support phrases внутри регистрации

## Что обязательно сделать после деплоя

После выката нужно:

1. применить migration `20260629_000004_unknown_intents`
2. проверить, что cron service в Render реально стартует
3. сделать 1-2 живых теста в WhatsApp
4. посмотреть:
   - `registration_debug_trace`
   - `unknown_intents`
   - `fallback_count`
   - `requires_attention`

Это даст уже не догадки, а точную картину, где бот ещё косячит на реальном трафике.

## Короткий итог

На этом этапе сделано главное:

- бот стал устойчивее к неправильному порядку фото/текста
- OCR больше не является одной слепой дырой
- застрявшие водители теперь выявляются автоматически
- support context теперь не висит бесконечно
- появился foundation для unknown intents и reminders

То есть это уже не просто “бот что-то отвечает”, а более управляемая система, которую можно дальше системно дожимать.
