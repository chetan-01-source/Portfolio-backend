from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class HireState(str, Enum):
    START = "START"
    ASK_NAME = "ASK_NAME"
    ASK_COMPANY = "ASK_COMPANY"
    ASK_CONTACT = "ASK_CONTACT"
    ASK_EMAIL = "ASK_EMAIL"
    ASK_ADDRESS = "ASK_ADDRESS"
    ASK_MESSAGE = "ASK_MESSAGE"
    ASK_SEND_DETAILS = "ASK_SEND_DETAILS"
    DONE = "DONE"


# Order of states the user is walked through.
HIRE_FIELD_ORDER: list[tuple[HireState, str]] = [
    (HireState.ASK_NAME, "name"),
    (HireState.ASK_COMPANY, "company"),
    (HireState.ASK_CONTACT, "contact"),
    (HireState.ASK_EMAIL, "email"),
    (HireState.ASK_ADDRESS, "address"),
    (HireState.ASK_MESSAGE, "message"),
    (HireState.ASK_SEND_DETAILS, "send_details"),
]

QUESTION_TEXT: dict[str, str] = {
    "name": "What's your name?",
    "company": "Which company are you with?",
    "contact": "What's the best phone number to reach you on? (include country code)",
    "email": "What's your email?",
    "address": "Where are you based? (city is fine — or skip)",
    "message": "Anything specific you'd like Chetan to know? (optional)",
    "send_details": "Want me to email you Chetan's full details (resume, portfolio, contact)?",
}

OPTIONAL_FIELDS = {"address", "message"}


# ---------------- HTTP I/O ----------------


class HireStartRequest(BaseModel):
    source: Literal["chat", "dock", "terminal:sudo-hire"] = "chat"
    website: str | None = Field(default=None, description="Honeypot — must be empty")


class HireAnswerRequest(BaseModel):
    session_id: str
    answer: str = Field(default="", max_length=600)
    website: str | None = Field(default=None, description="Honeypot")


class HireQuestionResponse(BaseModel):
    session_id: str
    question: str
    field: str
    choices: list[str] | None = None
    error: str | None = None
    lead_id: str | None = None


class HireDoneResponse(BaseModel):
    done: Literal[True] = True
    session_id: str
    lead_id: str
    emailed: bool


# ---------------- Domain models ----------------


class Lead(BaseModel):
    id: str | None = None
    name: str
    company: str
    contact: str
    email: str
    address: str | None = None
    message: str | None = None
    send_details_choice: Literal["yes", "no"] | None = None
    emailed: bool = False
    email_msgid: str | None = None
    emailed_at: datetime | None = None
    source: str
    ip: str
    user_agent: str
    created_at: datetime
    updated_at: datetime


class HireSession(BaseModel):
    id: str
    state: HireState
    answers: dict[str, str] = Field(default_factory=dict)
    lead_id: str | None = None
    ip: str
    user_agent: str
    created_at: datetime
    expires_at: datetime
