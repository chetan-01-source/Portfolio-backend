"""Per-field validators for the hire flow.

Each validator returns the canonicalized value or raises ValidationError.
Pure functions — no I/O, no DB, no settings.
"""

import re

import phonenumbers
from email_validator import EmailNotValidError, validate_email

URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


class ValidationError(ValueError):
    pass


def validate_name(value: str) -> str:
    v = value.strip()
    if not (2 <= len(v) <= 80):
        raise ValidationError("Name should be 2 to 80 characters.")
    if URL_RE.search(v):
        raise ValidationError("Name can't contain a URL.")
    return v


def validate_company(value: str) -> str:
    v = value.strip()
    if not (1 <= len(v) <= 120):
        raise ValidationError("Company should be 1 to 120 characters.")
    return v


def validate_contact(value: str) -> str:
    v = value.strip()
    try:
        parsed = phonenumbers.parse(v, None)
    except phonenumbers.NumberParseException:
        raise ValidationError(
            "Couldn't parse that number. Include country code, e.g. +91 98xxxxxx12."
        ) from None
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError("That phone number doesn't look valid.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def validate_email_field(value: str) -> str:
    v = value.strip()
    try:
        # check_deliverability does the MX lookup
        info = validate_email(v, check_deliverability=True)
    except EmailNotValidError as e:
        raise ValidationError(str(e)) from None
    return info.normalized


def validate_address(value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    if len(v) > 200:
        raise ValidationError("Address should be under 200 characters.")
    return v


def validate_message(value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    if len(v) > 600:
        raise ValidationError("Message should be under 600 characters.")
    return v


def validate_send_details(value: str) -> str:
    v = value.strip().lower()
    if v not in ("yes", "no", "y", "n"):
        raise ValidationError("Please answer yes or no.")
    return "yes" if v in ("yes", "y") else "no"


VALIDATORS = {
    "name": validate_name,
    "company": validate_company,
    "contact": validate_contact,
    "email": validate_email_field,
    "address": validate_address,
    "message": validate_message,
    "send_details": validate_send_details,
}
