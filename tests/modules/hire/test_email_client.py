from datetime import UTC, datetime

import pytest
from fastapi_mail import MessageType
from fastapi_mail.schemas import MultipartSubtypeEnum

from app.config import Settings
from app.modules.hire.email_client import EmailClient, _TRACKING_HEADER
from app.modules.hire.schemas import Lead


class _FakeFastMail:
    instances = []
    should_raise = False

    def __init__(self, config) -> None:
        self.config = config
        self.messages = []
        self.instances.append(self)

    async def send_message(self, message):
        if self.should_raise:
            raise RuntimeError("mail failed")
        self.messages.append(message)


@pytest.fixture(autouse=True)
def reset_fake_fastmail():
    _FakeFastMail.instances = []
    _FakeFastMail.should_raise = False


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
        "mail_username": "chetan.sender@gmail.com",
        "mail_password": "app-password",
        "mail_from": "chetan.sender@gmail.com",
        "mail_port": 587,
        "mail_server": "smtp.gmail.com",
        "mail_from_name": "CHET.ai",
        "mail_starttls": True,
        "mail_ssl_tls": False,
        "use_credentials": True,
        "validate_certs": True,
        "chetan_email": "chetan@example.com",
        "chetan_phone": "+919999999999",
        "chetan_resume_attachment_path": "",
        "include_phone_in_email": False,
        "notify_chetan_on_lead": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_connection_config_uses_mail_settings():
    client = EmailClient(settings())

    config = client._connection_config()

    assert config.MAIL_USERNAME == "chetan.sender@gmail.com"
    assert config.MAIL_PASSWORD.get_secret_value() == "app-password"
    assert config.MAIL_FROM == "chetan.sender@gmail.com"
    assert config.MAIL_PORT == 587
    assert config.MAIL_SERVER == "smtp.gmail.com"
    assert config.MAIL_FROM_NAME == "CHET.ai"
    assert config.MAIL_STARTTLS is True
    assert config.MAIL_SSL_TLS is False
    assert config.USE_CREDENTIALS is True
    assert config.VALIDATE_CERTS is True


async def test_send_chetan_details_sends_html_message_with_text_fallback(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.FastMail", _FakeFastMail)
    client = EmailClient(settings())

    msg_id = await client.send_chetan_details(lead)

    mailer = _FakeFastMail.instances[0]
    message = mailer.messages[0]
    assert msg_id == message.headers[_TRACKING_HEADER]
    assert message.recipients[0].email == "lead@example.com"
    assert message.reply_to[0].email == "chetan@example.com"
    assert message.subject == "Chetan Marathe — details you requested"
    assert message.subtype == MessageType.html
    assert message.multipart_subtype == MultipartSubtypeEnum.alternative
    assert "Chetan" in message.body
    assert "Email:" in message.alternative_body
    assert message.attachments == []


async def test_send_chetan_details_attaches_resume_pdf(monkeypatch, lead, tmp_path):
    monkeypatch.setattr("app.modules.hire.email_client.FastMail", _FakeFastMail)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.5\n")
    client = EmailClient(settings(chetan_resume_attachment_path=str(resume)))

    await client.send_chetan_details(lead)

    message = _FakeFastMail.instances[0].messages[0]
    assert message.recipients[0].email == "lead@example.com"
    assert len(message.attachments) == 1
    attachment, meta = message.attachments[0]
    assert attachment.filename == "resume.pdf"
    assert meta is None


async def test_notify_chetan_new_lead_sends_plain_internal_notification(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.FastMail", _FakeFastMail)
    client = EmailClient(settings())

    msg_id = await client.notify_chetan_new_lead(lead)

    message = _FakeFastMail.instances[0].messages[0]
    assert msg_id == message.headers[_TRACKING_HEADER]
    assert message.recipients[0].email == "chetan@example.com"
    assert message.reply_to[0].email == "lead@example.com"
    assert message.subject == "[chetanOS] New lead: Asha Kumar from Acme"
    assert message.subtype == MessageType.plain
    assert "New lead captured" in message.body


async def test_notify_chetan_new_lead_skips_when_disabled(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.FastMail", _FakeFastMail)
    client = EmailClient(settings(notify_chetan_on_lead=False))

    msg_id = await client.notify_chetan_new_lead(lead)

    assert msg_id is None
    assert _FakeFastMail.instances == []


async def test_send_chetan_details_returns_stub_when_mail_config_missing(lead):
    client = EmailClient(settings(mail_username="", mail_password="", mail_from=""))

    msg_id = await client.send_chetan_details(lead)

    assert msg_id == "stub-no-mail-config"


async def test_send_chetan_details_raises_on_mail_failure(monkeypatch, lead):
    monkeypatch.setattr("app.modules.hire.email_client.FastMail", _FakeFastMail)
    _FakeFastMail.should_raise = True
    client = EmailClient(settings())

    with pytest.raises(RuntimeError, match="mail failed"):
        await client.send_chetan_details(lead)
