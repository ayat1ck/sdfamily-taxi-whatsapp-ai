# n8n migration test scenarios for SD Family Taxi

Date: 2026-06-24

Use these scenarios to verify the AI Router, branch selection, and PostgreSQL state updates.

## 1. Payout support

### User message

"Как вывести деньги?"

### Expected

- intent: `payout_support`
- reply explains payout flow or routes to support
- no registration state change

## 2. Existing driver

### User message

"Я подключен уже"

### Expected

- intent: `existing_driver_support`
- do not start new registration
- keep existing driver profile

## 3. Human operator

### User message

"Оператор"

### Expected

- intent: `human_operator`
- manual mode enabled
- bot sends one handoff message and then stops replying automatically

## 4. Yandex Pro missing park

### User message

"Нет вашего таксопарка в Яндекс Про"

### Expected

- intent: `yandex_problem`
- support context created
- troubleshooting or escalation branch

## 5. Tariff disable issue

### User message

"Не могу отключить тариф"

### Expected

- intent: `tariff_support`
- if park action is needed, escalate

## 6. Enable Comfort

### User message

"Включите комфорт"

### Expected

- intent: `tariff_support`
- likely manual escalation or park action

## 7. Courier registration

### User message

"Зарегистрироваться хочу автокурьером"

### Expected

- intent: `courier_registration`
- route to courier branch or a dedicated registration branch

## 8. Rental car question

### User message

"У вас можно авто в аренду?"

### Expected

- intent: `rental_car_question`
- FAQ or support response

## 9. Blocking access

### User message

"Доступ ограничен"

### Expected

- intent: `blocking_priority_support`
- manual mode if needed
- escalation if the block prevents work

## 10. KGD says IP exists

### User message

"По данным КГД есть ИП"

### Expected

- intent: `faq` or `blocking_priority_support` depending on context
- likely manual review

## 11. Kazakh registration sentence

### User message

"Осы такса паркке тіркелейін деп едім"

### Expected

- intent: `registration`
- high confidence

## 12. Courier question in Kazakh

### User message

"Курьерка істеуге болады ма?"

### Expected

- intent: `courier_registration`

## 13. Screenshot after complaint

### Precondition

Support context exists.

### User message

An image or screenshot is sent.

### Expected

- treat as screenshot/problem attachment
- do not reject because it is not text

## 14. Photo during registration

### Precondition

Registration context exists.

### User message

An image or document is sent.

### Expected

- treat as registration document
- attempt extraction or store as document

## 15. Name correction

### Precondition

Registration or confirm context exists.

### User message

"Водитель хочет исправить ФИО"

### Expected

- intent: `driver_update_request`
- pending correction context set to full_name

## 16. Bonus FAQ

### User message

"Байга барма"

### Expected

- intent: `faq`
- support_topic: `bonus`

## 17. Tariff cities FAQ

### User message

"Таксопарк қай қалаларға істейді?"

### Expected

- intent: `faq`
- support_topic: `cities`

## 18. Self-employment request

### User message

"СМЗ жасап беріңіз"

### Expected

- intent: `smz_request`

## 19. Unknown message

### User message

"Ну и что теперь?"

### Expected

- intent: `unknown`
- safe clarification or manager handoff depending on context

## 20. Manual mode persistence

### Step

Send "Оператор", then send another normal message.

### Expected

- first message enables manual mode
- second message does not trigger auto-reply

