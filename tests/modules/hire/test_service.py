"""Hire service tests with an in-memory Mongo (mongomock-motor) and a stub mail client.

Verifies the critical behaviour: the lead is persisted BEFORE the consent question,
and `emailed` is set only on `send_details=yes` after the mail client returns.
"""

from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.modules.hire.repository import HireRepository
from app.modules.hire.schemas import HireDoneResponse, HireQuestionResponse
from app.modules.hire.service import HireService


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://stub",
        mail_username="",
        mail_password="",
        chetan_email="chetan@example.com",
    )


@pytest.fixture
def db():
    return AsyncMongoMockClient()["chet_ai_test"]


@pytest.fixture
def email_client():
    client = AsyncMock()
    client.send_chetan_details = AsyncMock(return_value="msg_stub_123")
    client.notify_chetan_new_lead = AsyncMock(return_value=None)
    return client


@pytest.fixture
def service(db, email_client, settings) -> HireService:
    return HireService(repo=HireRepository(db), email_client=email_client, settings=settings)


async def _walk(service: HireService, session_id: str, answers: dict[str, str]):
    """Walk through ASK_NAME → ASK_MESSAGE answering each in turn."""
    fields = ["name", "company", "contact", "email", "address", "message"]
    last: HireQuestionResponse | HireDoneResponse | None = None
    for field in fields:
        last = await service.handle_answer(
            session_id=session_id,
            answer=answers[field],
            ip="1.2.3.4",
            user_agent="UA",
            honeypot=None,
        )
    return last


async def test_lead_persisted_before_consent(service, db):
    started = await service.start(source="chat", ip="1.2.3.4", user_agent="UA", honeypot=None)
    assert started.field == "name"

    answers = {
        "name": "Asha Kumar",
        "company": "Acme",
        "contact": "+919820098200",
        "email": "chetanmarathe0412@gmail.com",
        "address": "Mumbai",
        "message": "Interested in a senior FE role",
    }
    last = await _walk(service, started.session_id, answers)

    assert isinstance(last, HireQuestionResponse)
    assert last.field == "send_details"
    assert last.lead_id is not None  # lead already saved
    assert last.choices == ["yes", "no"]

    # Lead doc exists in Mongo with consent unset, emailed=false.
    docs = [d async for d in db.leads.find({})]
    assert len(docs) == 1
    assert docs[0]["send_details_choice"] is None
    assert docs[0]["emailed"] is False
    assert docs[0]["name"] == "Asha Kumar"


async def test_consent_yes_triggers_email(service, db, email_client):
    started = await service.start(source="chat", ip="1.2.3.4", user_agent="UA", honeypot=None)
    answers = {
        "name": "Asha Kumar",
        "company": "Acme",
        "contact": "+919820098200",
        "email": "chetanmarathe0412@gmail.com",
        "address": "",
        "message": "",
    }
    await _walk(service, started.session_id, answers)
    done = await service.handle_answer(
        session_id=started.session_id, answer="yes", ip="1.2.3.4", user_agent="UA", honeypot=None
    )
    assert isinstance(done, HireDoneResponse)
    assert done.emailed is True
    email_client.send_chetan_details.assert_awaited_once()

    docs = [d async for d in db.leads.find({})]
    assert docs[0]["emailed"] is True
    assert docs[0]["send_details_choice"] == "yes"
    assert docs[0]["email_msgid"] == "msg_stub_123"


async def test_message_requesting_details_auto_sends_email(service, db, email_client):
    started = await service.start(source="chat", ip="1.2.3.4", user_agent="UA", honeypot=None)
    answers = {
        "name": "Asha Kumar",
        "company": "Acme",
        "contact": "+919820098200",
        "email": "chetanmarathe0412@gmail.com",
        "address": "",
        "message": "Just share resume and details",
    }

    done = await _walk(service, started.session_id, answers)

    assert isinstance(done, HireDoneResponse)
    assert done.emailed is True
    email_client.send_chetan_details.assert_awaited_once()

    docs = [d async for d in db.leads.find({})]
    assert docs[0]["emailed"] is True
    assert docs[0]["send_details_choice"] == "yes"
    assert docs[0]["email_msgid"] == "msg_stub_123"


async def test_consent_no_does_not_email(service, db, email_client):
    started = await service.start(source="chat", ip="1.2.3.4", user_agent="UA", honeypot=None)
    answers = {
        "name": "Asha Kumar",
        "company": "Acme",
        "contact": "+919820098200",
        "email": "chetanmarathe0412@gmail.com",
        "address": "",
        "message": "",
    }
    await _walk(service, started.session_id, answers)
    done = await service.handle_answer(
        session_id=started.session_id, answer="no", ip="1.2.3.4", user_agent="UA", honeypot=None
    )
    assert isinstance(done, HireDoneResponse)
    assert done.emailed is False
    email_client.send_chetan_details.assert_not_awaited()

    docs = [d async for d in db.leads.find({})]
    assert docs[0]["emailed"] is False
    assert docs[0]["send_details_choice"] == "no"


async def test_invalid_answer_does_not_advance(service):
    started = await service.start(source="chat", ip="1.2.3.4", user_agent="UA", honeypot=None)
    res = await service.handle_answer(
        session_id=started.session_id, answer="X", ip="-", user_agent="-", honeypot=None
    )
    assert isinstance(res, HireQuestionResponse)
    assert res.field == "name"
    assert res.error is not None
