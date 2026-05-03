"""Hire-flow business logic. State machine + persist-then-consent.

Order of operations matches plan §8:
  ASK_NAME → ASK_COMPANY → ASK_CONTACT → ASK_EMAIL → ASK_ADDRESS → ASK_MESSAGE
    → PERSIST_LEAD (non-interactive: writes to Mongo, returns lead_id)
    → ASK_SEND_DETAILS → (SEND_EMAIL if yes) → DONE

If the freeform message explicitly asks to send/share Chetan's resume,
portfolio, contact, or details, that message is treated as consent and the
details email is sent immediately after the lead is persisted.

The lead is saved BEFORE the consent question so abandonment after that point
still leaves a recorded lead.
"""

import re
import uuid

from app.config import Settings
from app.core.logging import logger
from app.modules.hire.email_client import EmailClient
from app.modules.hire.repository import HireRepository
from app.modules.hire.schemas import (
    HIRE_FIELD_ORDER,
    OPTIONAL_FIELDS,
    QUESTION_TEXT,
    HireDoneResponse,
    HireQuestionResponse,
    HireSession,
    HireState,
)
from app.modules.hire.validators import VALIDATORS, ValidationError


class HireFlowError(Exception):
    pass


class SessionNotFound(HireFlowError):
    pass


class HoneypotTriggered(HireFlowError):
    pass


_DETAILS_REQUEST_RE = re.compile(
    r"""
    \b(?:send|share|mail|email|forward)\b.{0,50}\b(?:resume|cv|portfolio|details|contact)\b
    | \b(?:resume|cv|portfolio|details|contact)\b.{0,50}\b(?:send|share|mail|email|forward)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DETAILS_NEGATION_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|not|no)\b.{0,40}\b(?:send|share|mail|email|forward)\b",
    re.IGNORECASE,
)


def _message_requests_details(message: str | None) -> bool:
    if not message:
        return False
    if _DETAILS_NEGATION_RE.search(message):
        return False
    return bool(_DETAILS_REQUEST_RE.search(message))


def _next_state(current: HireState) -> tuple[HireState, str | None]:
    """Return (next_state, field_to_ask). field is None if state is terminal."""
    states = [s for s, _ in HIRE_FIELD_ORDER]
    try:
        idx = states.index(current)
    except ValueError:
        return HireState.DONE, None
    if idx + 1 >= len(states):
        return HireState.DONE, None
    next_s, next_field = HIRE_FIELD_ORDER[idx + 1]
    return next_s, next_field


def _field_for_state(state: HireState) -> str | None:
    for s, f in HIRE_FIELD_ORDER:
        if s == state:
            return f
    return None


class HireService:
    def __init__(
        self,
        repo: HireRepository,
        email_client: EmailClient,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.email = email_client
        self.settings = settings

    async def start(
        self,
        *,
        source: str,
        ip: str,
        user_agent: str,
        honeypot: str | None,
    ) -> HireQuestionResponse:
        if honeypot:
            raise HoneypotTriggered("honeypot triggered")
        session_id = str(uuid.uuid4())
        await self.repo.create_session(
            session_id=session_id,
            ip=ip,
            user_agent=user_agent,
            ttl_seconds=self.settings.hire_session_ttl_seconds,
        )
        # Stash the source on the session via a synthetic answer so persist can read it.
        await self.repo.update_session(
            session_id, state=HireState.ASK_NAME, answers={"_source": source}
        )
        return HireQuestionResponse(
            session_id=session_id,
            question=QUESTION_TEXT["name"],
            field="name",
        )

    async def handle_answer(
        self,
        *,
        session_id: str,
        answer: str,
        ip: str,
        user_agent: str,
        honeypot: str | None,
    ) -> HireQuestionResponse | HireDoneResponse:
        if honeypot:
            raise HoneypotTriggered("honeypot triggered")

        session = await self.repo.get_session(session_id)
        if not session:
            raise SessionNotFound(session_id)

        current_field = _field_for_state(session.state)
        if not current_field:
            # Already DONE — return a friendly terminal payload.
            return HireDoneResponse(
                session_id=session.id,
                lead_id=session.lead_id or "",
                emailed=False,
            )

        # Validate
        try:
            value = VALIDATORS[current_field](answer)
        except ValidationError as e:
            return HireQuestionResponse(
                session_id=session.id,
                question=QUESTION_TEXT[current_field],
                field=current_field,
                error=str(e),
                lead_id=session.lead_id,
                choices=["yes", "no"] if current_field == "send_details" else None,
            )

        # Empty optional → store as None but keep advancing.
        answers = dict(session.answers)
        if value is None and current_field in OPTIONAL_FIELDS:
            answers[current_field] = ""
        else:
            answers[current_field] = value

        # Branch: special handling for send_details (which requires the lead to exist).
        if current_field == "send_details":
            return await self._finalize(session, answers, value)

        # Standard advance
        next_state, next_field = _next_state(session.state)

        # If the next state is ASK_SEND_DETAILS, persist the lead first.
        if next_state == HireState.ASK_SEND_DETAILS:
            lead = await self._persist_lead(session, answers)

            if current_field == "message" and _message_requests_details(answers.get("message")):
                answers["send_details"] = "yes"
                session_with_lead = session.model_copy(update={"lead_id": lead.id})
                done = await self._finalize(session_with_lead, answers, "yes")
                await self._notify_chetan_new_lead(lead.id)
                return done

            await self.repo.update_session(
                session.id, state=next_state, answers=answers, lead_id=lead.id
            )
            # Internal notification is best-effort; a mail failure must not block lead capture.
            await self._notify_chetan_new_lead(lead.id, fallback=lead)
            return HireQuestionResponse(
                session_id=session.id,
                question=QUESTION_TEXT["send_details"],
                field="send_details",
                choices=["yes", "no"],
                lead_id=lead.id,
            )

        await self.repo.update_session(session.id, state=next_state, answers=answers)
        return HireQuestionResponse(
            session_id=session.id,
            question=QUESTION_TEXT[next_field] if next_field else "",
            field=next_field or "",
            lead_id=session.lead_id,
        )

    # ---------------- internals ----------------

    async def _notify_chetan_new_lead(self, lead_id: str | None, fallback=None) -> None:
        lead = await self.repo.get_lead(lead_id) if lead_id else fallback
        if lead is None:
            return
        try:
            await self.email.notify_chetan_new_lead(lead)
        except Exception as e:
            logger.warning(f"internal notify failed: {e}")

    async def _persist_lead(self, session: HireSession, answers: dict):
        source = answers.get("_source") or "chat"
        clean = {k: v for k, v in answers.items() if not k.startswith("_")}
        return await self.repo.insert_lead(
            answers=clean,
            source=source,
            ip=session.ip,
            user_agent=session.user_agent,
        )

    async def _finalize(
        self, session: HireSession, answers: dict, choice: str
    ) -> HireDoneResponse:
        if not session.lead_id:
            # Defensive: should never happen because PERSIST runs at ASK_SEND_DETAILS transition.
            lead = await self._persist_lead(session, answers)
            session_lead_id = lead.id
        else:
            session_lead_id = session.lead_id

        await self.repo.set_consent(session_lead_id, choice)

        emailed = False
        if choice == "yes":
            lead = await self.repo.get_lead(session_lead_id)
            if lead is None:
                raise HireFlowError(f"lead {session_lead_id} disappeared")
            try:
                msg_id = await self.email.send_chetan_details(lead)
                await self.repo.mark_emailed(session_lead_id, msg_id)
                emailed = True
            except Exception as e:
                logger.exception(f"send_chetan_details failed for lead {session_lead_id}: {e}")
                # Lead is still saved; emailed stays false.

        await self.repo.update_session(
            session.id, state=HireState.DONE, answers=answers, lead_id=session_lead_id
        )
        return HireDoneResponse(
            session_id=session.id,
            lead_id=session_lead_id,
            emailed=emailed,
        )
