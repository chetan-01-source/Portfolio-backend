"""fastapi-mail email client. Composes and sends two email types:

1. `send_chetan_details(...)` — to the lead, with Chetan's resume/portfolio/contact.
2. `notify_chetan_new_lead(...)` — to Chetan, internal notification.

Returns the generated tracking ID on success, raises on delivery failure.
"""

from email.utils import make_msgid
from pathlib import Path

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from fastapi_mail.schemas import MultipartSubtypeEnum
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.core.logging import logger
from app.modules.hire.schemas import Lead

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_TRACKING_HEADER = "X-CHET-Message-ID"

_jinja = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _is_configured(self) -> bool:
        s = self.settings
        return bool(
            s.mail_username
            and s.mail_password
            and s.mail_from
            and s.mail_port
            and s.mail_server
        )

    def _message_id(self) -> str:
        s = self.settings
        domain = s.mail_from.rsplit("@", 1)[-1] if "@" in s.mail_from else "localhost"
        return make_msgid(domain=domain)

    def _connection_config(self) -> ConnectionConfig:
        s = self.settings
        return ConnectionConfig(
            MAIL_USERNAME=s.mail_username,
            MAIL_PASSWORD=s.mail_password,
            MAIL_FROM=s.mail_from,
            MAIL_PORT=s.mail_port,
            MAIL_SERVER=s.mail_server,
            MAIL_FROM_NAME=s.mail_from_name,
            MAIL_STARTTLS=s.mail_starttls,
            MAIL_SSL_TLS=s.mail_ssl_tls,
            USE_CREDENTIALS=s.use_credentials,
            VALIDATE_CERTS=s.validate_certs,
        )

    def _resume_attachments(self) -> list[str]:
        raw_path = self.settings.chetan_resume_attachment_path.strip()
        if not raw_path:
            return []

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if not path.is_file():
            logger.warning(
                f"Resume attachment not found at {path}; sending details email with links only"
            )
            return []

        return [str(path)]

    async def _send_message(self, message: MessageSchema, message_id: str) -> str:
        if not self._is_configured():
            logger.warning("Mail config missing — skipping send")
            return "stub-no-mail-config"

        fm = FastMail(self._connection_config())
        await fm.send_message(message)
        return message_id

    def _build_message(
        self,
        *,
        to: str,
        reply_to: str,
        subject: str,
        text: str,
        html: str | None = None,
        attachments: list[str] | None = None,
    ) -> tuple[MessageSchema, str]:
        message_id = self._message_id()
        body = html or text
        subtype = MessageType.html if html else MessageType.plain
        alternative_body = text if html else None
        multipart_subtype = (
            MultipartSubtypeEnum.alternative if alternative_body else MultipartSubtypeEnum.mixed
        )
        message = MessageSchema(
            recipients=[to],
            reply_to=[reply_to],
            subject=subject,
            body=body,
            alternative_body=alternative_body,
            subtype=subtype,
            multipart_subtype=multipart_subtype,
            attachments=attachments or [],
            headers={_TRACKING_HEADER: message_id},
        )
        return message, message_id

    async def send_chetan_details(self, lead: Lead) -> str:
        s = self.settings
        ctx = {
            "name": lead.name,
            "company": lead.company,
            "email": s.chetan_email,
            "phone": s.chetan_phone if s.include_phone_in_email else "",
            "resume_url": s.chetan_resume_url,
            "portfolio_url": s.chetan_portfolio_url,
            "linkedin_url": s.chetan_linkedin_url,
            "github_url": s.chetan_github_url,
            "leetcode_url": s.chetan_leetcode_url,
        }
        html = _jinja.get_template("chetan_details.html").render(**ctx)
        text = _jinja.get_template("chetan_details.txt").render(**ctx)

        message, message_id = self._build_message(
            to=lead.email,
            reply_to=s.chetan_email,
            subject="Chetan Marathe — details you requested",
            text=text,
            html=html,
            attachments=self._resume_attachments(),
        )
        msg_id = await self._send_message(message, message_id)
        logger.info(f"Sent details email to {lead.email} (msg_id={msg_id})")
        return msg_id

    async def notify_chetan_new_lead(self, lead: Lead) -> str | None:
        s = self.settings
        if not s.notify_chetan_on_lead or not self._is_configured():
            return None
        details_status = lead.send_details_choice or "pending (visitor has not answered yet)"
        if lead.send_details_choice == "yes":
            details_status = "yes, emailed" if lead.emailed else "yes, email not sent yet"
        body = (
            f"New lead captured on {s.chetan_portfolio_url}\n\n"
            f"Name:    {lead.name}\n"
            f"Company: {lead.company}\n"
            f"Email:   {lead.email}\n"
            f"Phone:   {lead.contact}\n"
            f"Address: {lead.address or '-'}\n"
            f"Message: {lead.message or '-'}\n"
            f"Source:  {lead.source}\n"
            f"Wanted details emailed at notification time: {details_status}\n\n"
            f"This is an internal notification. Use Reply to email the lead directly.\n"
        )
        message, message_id = self._build_message(
            to=s.chetan_email,
            reply_to=lead.email,
            subject=f"[chetanOS] New lead: {lead.name} from {lead.company}",
            text=body,
        )
        try:
            return await self._send_message(message, message_id)
        except Exception as e:
            logger.warning(f"notify_chetan_new_lead failed: {e}")
            return None
