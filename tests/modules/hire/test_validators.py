import pytest

from app.modules.hire.validators import (
    ValidationError,
    validate_company,
    validate_contact,
    validate_message,
    validate_name,
    validate_send_details,
)


def test_name_ok():
    assert validate_name("  Asha Kumar ") == "Asha Kumar"


def test_name_rejects_url():
    with pytest.raises(ValidationError):
        validate_name("https://spam.example/asha")


def test_name_too_short():
    with pytest.raises(ValidationError):
        validate_name("A")


def test_company_ok():
    assert validate_company(" Acme ") == "Acme"


def test_contact_e164_normalized():
    # Valid Indian number formatted with country code.
    assert validate_contact("+91 9820098200") == "+919820098200"


def test_contact_invalid():
    with pytest.raises(ValidationError):
        validate_contact("not a phone")


def test_message_optional_empty():
    assert validate_message("") is None


def test_message_too_long():
    with pytest.raises(ValidationError):
        validate_message("x" * 601)


@pytest.mark.parametrize(
    "raw, expected",
    [("yes", "yes"), ("Y", "yes"), (" NO ", "no"), ("n", "no")],
)
def test_send_details_normalizes(raw, expected):
    assert validate_send_details(raw) == expected


def test_send_details_invalid():
    with pytest.raises(ValidationError):
        validate_send_details("maybe")
