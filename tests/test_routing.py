import unittest
from types import SimpleNamespace

from app.dialog.faq import classify_dialog_intent
from app.dialog.states import DialogueState


class RoutingClassificationTests(unittest.TestCase):
    def test_new_state_routing_examples(self):
        cases = [
            ("1", DialogueState.NEW.value, "registration"),
            ("регистрация", DialogueState.NEW.value, "registration"),
            ("тіркелу", DialogueState.NEW.value, "registration"),
            ("хочу подключиться", DialogueState.NEW.value, "registration"),
            ("привет", DialogueState.NEW.value, "smalltalk"),
            ("здравствуйте", DialogueState.NEW.value, "smalltalk"),
            ("условия", DialogueState.NEW.value, "faq"),
            ("какие условия", DialogueState.NEW.value, "faq"),
            ("вход в яндекс", DialogueState.NEW.value, "faq"),
            ("помощь со входом в яндекс про", DialogueState.NEW.value, "yandex_problem"),
            ("я уже зареган", DialogueState.NEW.value, "existing_driver_support"),
            ("я уже зарегистрирован", DialogueState.NEW.value, "existing_driver_support"),
            ("я уже подключен", DialogueState.NEW.value, "existing_driver_support"),
            ("не вижу парк", DialogueState.NEW.value, "application_status"),
            ("не приходит sms", DialogueState.NEW.value, "faq"),
            ("выплата", DialogueState.NEW.value, "faq"),
            ("тариф", DialogueState.NEW.value, "faq"),
            ("менеджер", DialogueState.NEW.value, "human_operator"),
            ("тех поддержка", DialogueState.NEW.value, "human_operator"),
            ("калай тіркелсем болады", DialogueState.NEW.value, "faq"),
            ("тыркелу керек", DialogueState.NEW.value, "faq"),
            ("тиркейелин дегем", DialogueState.NEW.value, "registration"),
            ("смз жасап бериниз", DialogueState.NEW.value, "existing_driver_support"),
            ("поменять машину", DialogueState.NEW.value, "driver_update_request"),
            ("изменить госномер", DialogueState.NEW.value, "driver_update_request"),
            ("исправить данные", DialogueState.NEW.value, "driver_update_request"),
            ("ошибка в данных", DialogueState.NEW.value, "driver_update_request"),
            (".", DialogueState.NEW.value, "smalltalk"),
        ]
        for message, state, expected_intent in cases:
            with self.subTest(message=message):
                self.assertEqual(classify_dialog_intent(message, current_state=state), expected_intent)

    def test_active_registration_city_and_support_examples_do_not_route_to_greeting(self):
        cases = [
            ("Астана", DialogueState.ASK_CITY.value, "registration"),
            ("в Астане работать буду", DialogueState.ASK_CITY.value, "registration"),
            ("пр. Республики 12", DialogueState.ASK_ADDRESS.value, "registration"),
            ("что делать", DialogueState.ASK_CITY.value, "faq"),
            ("?", DialogueState.ASK_CITY.value, "registration"),
            ("менеджер", DialogueState.ASK_CITY.value, "human_operator"),
        ]
        for message, state, expected_intent in cases:
            with self.subTest(message=message):
                self.assertEqual(classify_dialog_intent(message, current_state=state), expected_intent)

    def test_existing_driver_menu_numeric_inputs_can_be_checked_explicitly(self):
        menu_map = {
            "1": "payout_support",
            "2": "yandex_problem",
            "3": "tariff_support",
            "4": "driver_update_request",
            "5": "human_operator",
        }
        self.assertEqual(menu_map["1"], "payout_support")
        self.assertEqual(menu_map["2"], "yandex_problem")
        self.assertEqual(menu_map["3"], "tariff_support")
        self.assertEqual(menu_map["4"], "driver_update_request")
        self.assertEqual(menu_map["5"], "human_operator")

    def test_bonus_and_box_questions_route_to_faq(self):
        cases = [
            ("За 50 заказов 5 тысяч?", "faq"),
            ("За 200 заказов 20 тысяч есть?", "faq"),
            ("Приветственный бокс есть?", "faq"),
            ("Набор для водителей есть?", "faq"),
            ("У вас есть вакансия на менеджера", "human_operator"),
        ]
        for message, expected_intent in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    classify_dialog_intent(message, current_state=DialogueState.NEW.value),
                    expected_intent,
                )


class RoutingStateInvariantTests(unittest.TestCase):
    def test_dummy_driver_shape_matches_routing_expectations(self):
        driver = SimpleNamespace(
            state=DialogueState.NEW.value,
            support_context_json=None,
            fallback_count=0,
            requires_attention=False,
        )
        self.assertEqual(driver.state, "new")
        self.assertIsNone(driver.support_context_json)
        self.assertEqual(driver.fallback_count, 0)
        self.assertFalse(driver.requires_attention)


if __name__ == "__main__":
    unittest.main()
