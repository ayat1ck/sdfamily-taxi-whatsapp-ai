from app.integrations.yandex.messages import format_yandex_error_for_user, manager_phone


def test_format_duplicate_driver_license_api_error() -> None:
    raw = "Yandex API error 400: code=duplicate_driver_license, message=duplicate_driver_license"
    message = format_yandex_error_for_user(raw)
    assert "Yandex API error" not in message
    assert "duplicate_driver_license" not in message
    assert "водительского удостоверения" in message
    assert manager_phone() in message


def test_format_unknown_yandex_api_error() -> None:
    raw = "Yandex API error 500: code=internal_error, message=something broke"
    message = format_yandex_error_for_user(raw)
    assert "Yandex API error" not in message
    assert "internal_error" not in message
    assert "ошибка на стороне парка" in message
    assert manager_phone() in message