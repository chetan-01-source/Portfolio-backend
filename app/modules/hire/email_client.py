"""SendGrid Web API email client for the hire flow.

Sends two email types:

1. `send_chetan_details(...)` — to the lead, using Chetan's SendGrid dynamic template.
2. `notify_chetan_new_lead(...)` — to Chetan, as a plain-text internal notification.

Returns the generated tracking ID on success, raises on delivery failure.
"""

import asyncio
import base64
import mimetypes
from email.utils import make_msgid
from pathlib import Path

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment,
    Disposition,
    Email,
    FileContent,
    FileName,
    FileType,
    Header,
    Mail,
    ReplyTo,
    To,
)

from app.config import Settings
from app.core.logging import logger
from app.modules.hire.schemas import Lead

_TRACKING_HEADER = "X-CHET-Message-ID"
_ACCEPTED_STATUS_CODES = {200, 201, 202}


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _from_email(self) -> str:
        return (
            self.settings.sendgrid_from_email.strip()
            or self.settings.mail_from.strip()
            or self.settings.chetan_email.strip()
        )

    def _is_configured(self, *, require_template: bool = False) -> bool:
        configured = bool(self.settings.sendgrid_api_key and self._from_email())
        if require_template:
            configured = configured and bool(self.settings.sendgrid_template_id)
        return configured

    def _message_id(self) -> str:
        from_email = self._from_email()
        domain = from_email.rsplit("@", 1)[-1] if "@" in from_email else "localhost"
        return make_msgid(domain=domain)

    def _client(self) -> SendGridAPIClient:
        sg = SendGridAPIClient(self.settings.sendgrid_api_key)
        data_residency = self.settings.sendgrid_data_residency.strip().lower()
        if data_residency:
            sg.set_sendgrid_data_residency(data_residency)
        return sg

    def _sender(self) -> Email:
        name = self.settings.sendgrid_from_name.strip() or self.settings.mail_from_name
        return Email(self._from_email(), name)

    def _resume_attachment(self) -> Attachment | None:
        raw_path = self.settings.chetan_resume_attachment_path.strip()
        if not raw_path:
            return None

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if not path.is_file():
            logger.warning(
                f"Resume attachment not found at {path}; sending details email with links only"
            )
            return None

        mime_type, _ = mimetypes.guess_type(path.name)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return Attachment(
            FileContent(encoded),
            FileName(path.name),
            FileType(mime_type or "application/pdf"),
            Disposition("attachment"),
        )

    async def _send_message(
        self,
        message: Mail,
        message_id: str,
        *,
        require_template: bool = False,
    ) -> str:
        if not self._is_configured(require_template=require_template):
            logger.warning("SendGrid config missing — skipping send")
            return "stub-no-mail-config"

        sg = self._client()
        response = await asyncio.to_thread(sg.send, message)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in _ACCEPTED_STATUS_CODES:
            body = getattr(response, "body", b"")
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            raise RuntimeError(f"SendGrid send failed with status {status_code}: {body}")
        return message_id

    def _base_message(
        self,
        *,
        to: str,
        to_name: str | None = None,
        reply_to: str,
        subject: str,
        plain_text_content: str | None = None,
    ) -> tuple[Mail, str]:
        message_id = self._message_id()
        message = Mail(
            from_email=self._sender(),
            to_emails=To(to, to_name),
            subject=subject,
            plain_text_content=plain_text_content,
        )
        message.reply_to = ReplyTo(reply_to)
        message.add_header(Header(_TRACKING_HEADER, message_id))
        return message, message_id

    async def send_chetan_details(self, lead: Lead) -> str:
        s = self.settings
        message, message_id = self._base_message(
            to=lead.email,
            to_name=lead.name,
            reply_to=s.chetan_email,
            subject="Chetan Marathe - details you requested",
        )
        message.template_id = s.sendgrid_template_id
        message.dynamic_template_data = {
            "leadName": lead.name,
            "name": lead.name,
            "company": lead.company,
            "email": s.chetan_email,
            "phone": s.chetan_phone if s.include_phone_in_email else "",
            "resumeUrl": s.chetan_resume_url,
            "resume_url": s.chetan_resume_url,
            "portfolioUrl": s.chetan_portfolio_url,
            "portfolio_url": s.chetan_portfolio_url,
            "linkedinUrl": s.chetan_linkedin_url,
            "linkedin_url": s.chetan_linkedin_url,
            "githubUrl": s.chetan_github_url,
            "github_url": s.chetan_github_url,
            "leetcodeUrl": s.chetan_leetcode_url,
            "leetcode_url": s.chetan_leetcode_url,
        }

        attachment = self._resume_attachment()
        if attachment:
            message.add_attachment(attachment)

        msg_id = await self._send_message(message, message_id, require_template=True)
        logger.info(f"Sent SendGrid details email to {lead.email} (msg_id={msg_id})")
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
        message, message_id = self._base_message(
            to=s.chetan_email,
            reply_to=lead.email,
            subject=f"[chetanOS] New lead: {lead.name} from {lead.company}",
            plain_text_content=body,
        )
        try:
            return await self._send_message(message, message_id)
        except Exception as e:
            logger.warning(f"notify_chetan_new_lead failed: {e}")
            return None
