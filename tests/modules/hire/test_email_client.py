import base64
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.modules.hire.email_client import _TRACKING_HEADER, EmailClient
from app.modules.hire.schemas import Lead


class _FakeSendGridClient:
    instances = []
    should_raise = False
    response = SimpleNamespace(status_code=202, body=b"", headers={})

    def __init__(self, api_key) -> None:
        self.api_key = api_key
        self.messages = []
        self.data_residency = None
        self.instances.append(self)

    def set_sendgrid_data_residency(self, region):
        self.data_residency = region

    def send(self, message):
        if self.should_raise:
            raise RuntimeError("mail failed")
        self.messages.append(message)
        return self.response


@pytest.fixture(autouse=True)
def reset_fake_sendgrid():
    _FakeSendGridClient.instances = []
    _FakeSendGridClient.should_raise = False
    _FakeSendGridClient.response = SimpleNamespace(status_code=202, body=b"", headers={})


@pytest.fixture
def lead() -> Lead:
    now = datetime.now(UTC)
    return Lead(
        id="lead123",
        name="Asha Kumar",
        company="Acme",
        contact="+919820098200",
        email="lead@example.com",
        address="Mumbai",
        message="Interested in a senior FE role",
        source="chat",
        ip="1.2.3.4",
        user_agent="UA",
        created_at=now,
        updated_at=now,
    )


def settings(**overrides) -> Settings:
    values = {
        "sendgrid_api_key": "SG.test-key",
        "sendgrid_template_id": "d-8ef38ee86ac8492aaba9413a8e8be01b",
        "sendgrid_from_email": "chetan.sender@example.com",
        "sendgrid_from_name": "CHET.ai",
        "sendgrid_data_residency": "",
        "mail_from": "",
        "mail_from_name": "CHET.ai",
        "chetan_email": "chetan@example.com",
        "chetan_phone": "+919999999999",
        "chetan_resume_attachment_path": "",
        "include_phone_in_email": False,
        "notify_chetan_on_lead": True,
    }
    values.update(overrides)
    return Settings(**values)


async def test_send_chetan_details_uses_sendgrid_dynamic_template(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    client = EmailClient(settings())

    msg_id = await client.send_chetan_details(lead)

    sendgrid = _FakeSendGridClient.instances[0]
    payload = sendgrid.messages[0].get()
    assert sendgrid.api_key == "SG.test-key"
    assert msg_id == payload["headers"][_TRACKING_HEADER]
    assert payload["from"] == {"email": "chetan.sender@example.com", "name": "CHET.ai"}
    assert payload["personalizations"][0]["to"][0] == {
        "email": "lead@example.com",
        "name": "Asha Kumar",
    }
    assert payload["reply_to"] == {"email": "chetan@example.com"}
    assert payload["subject"] == "Chetan Marathe - details you requested"
    assert payload["template_id"] == "d-8ef38ee86ac8492aaba9413a8e8be01b"
    dynamic_data = payload["personalizations"][0]["dynamic_template_data"]
    assert dynamic_data["leadName"] == "Asha Kumar"
    assert dynamic_data["name"] == "Asha Kumar"
    assert dynamic_data["company"] == "Acme"
    assert dynamic_data["portfolioUrl"]
    assert "content" not in payload


async def test_send_chetan_details_attaches_resume_pdf(monkeypatch, lead, tmp_path):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.5\n")
    client = EmailClient(settings(chetan_resume_attachment_path=str(resume)))

    await client.send_chetan_details(lead)

    payload = _FakeSendGridClient.instances[0].messages[0].get()
    attachment = payload["attachments"][0]
    assert attachment["filename"] == "resume.pdf"
    assert attachment["type"] == "application/pdf"
    assert attachment["disposition"] == "attachment"
    assert base64.b64decode(attachment["content"].encode("ascii")) == b"%PDF-1.5\n"


async def test_send_chetan_details_sets_data_residency(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    client = EmailClient(settings(sendgrid_data_residency="eu"))

    await client.send_chetan_details(lead)

    assert _FakeSendGridClient.instances[0].data_residency == "eu"


async def test_notify_chetan_new_lead_sends_plain_internal_notification(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    client = EmailClient(settings())

    msg_id = await client.notify_chetan_new_lead(lead)

    payload = _FakeSendGridClient.instances[0].messages[0].get()
    assert msg_id == payload["headers"][_TRACKING_HEADER]
    assert payload["personalizations"][0]["to"][0]["email"] == "chetan@example.com"
    assert payload["reply_to"] == {"email": "lead@example.com"}
    assert payload["subject"] == "[chetanOS] New lead: Asha Kumar from Acme"
    assert payload["content"][0]["type"] == "text/plain"
    assert "New lead captured" in payload["content"][0]["value"]


async def test_notify_chetan_new_lead_skips_when_disabled(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    client = EmailClient(settings(notify_chetan_on_lead=False))

    msg_id = await client.notify_chetan_new_lead(lead)

    assert msg_id is None
    assert _FakeSendGridClient.instances == []


async def test_send_chetan_details_returns_stub_when_sendgrid_config_missing(lead):
    client = EmailClient(settings(sendgrid_api_key="", sendgrid_from_email=""))

    msg_id = await client.send_chetan_details(lead)

    assert msg_id == "stub-no-mail-config"


async def test_send_chetan_details_raises_on_mail_failure(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    _FakeSendGridClient.should_raise = True
    client = EmailClient(settings())

    with pytest.raises(RuntimeError, match="mail failed"):
        await client.send_chetan_details(lead)


async def test_send_chetan_details_raises_on_rejected_response(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.SendGridAPIClient", _FakeSendGridClient)
    _FakeSendGridClient.response = SimpleNamespace(
        status_code=401,
        body=b'{"errors":[{"message":"bad api key"}]}',
        headers={},
    )
    client = EmailClient(settings())

    with pytest.raises(RuntimeError, match="status 401"):
        await client.send_chetan_details(lead)
