"""All MongoDB I/O for the hire module. No business logic lives here."""

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.hire.schemas import HireSession, HireState, Lead


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lead_from_doc(doc: dict) -> Lead:
    doc = {**doc}
    doc["id"] = str(doc.pop("_id"))
    return Lead.model_validate(doc)


def _session_from_doc(doc: dict) -> HireSession:
    return HireSession.model_validate(doc)


class HireRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db
        self.leads = db.leads
        self.sessions = db.hire_sessions

    # ---------------- sessions ----------------

    async def create_session(
        self, *, session_id: str, ip: str, user_agent: str, ttl_seconds: int
    ) -> HireSession:
        now = _utcnow()
        doc = {
            "_id": session_id,
            "state": HireState.ASK_NAME.value,
            "answers": {},
            "lead_id": None,
            "ip": ip,
            "user_agent": user_agent,
            "created_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
        }
        await self.sessions.insert_one(doc)
        return HireSession.model_validate({**doc, "id": session_id})

    async def get_session(self, session_id: str) -> HireSession | None:
        doc = await self.sessions.find_one({"_id": session_id})
        if not doc:
            return None
        doc["id"] = doc.pop("_id")
        return _session_from_doc(doc)

    async def update_session(
        self, session_id: str, *, state: HireState, answers: dict, lead_id: str | None = None
    ) -> None:
        update = {"state": state.value, "answers": answers}
        if lead_id is not None:
            update["lead_id"] = lead_id
        await self.sessions.update_one({"_id": session_id}, {"$set": update})

    async def delete_session(self, session_id: str) -> None:
        await self.sessions.delete_one({"_id": session_id})

    # ---------------- leads ----------------

    async def insert_lead(
        self,
        *,
        answers: dict,
        source: str,
        ip: str,
        user_agent: str,
    ) -> Lead:
        now = _utcnow()
        doc = {
            "name": answers["name"],
            "company": answers["company"],
            "contact": answers["contact"],
            "email": answers["email"],
            "address": answers.get("address"),
            "message": answers.get("message"),
            "send_details_choice": None,
            "emailed": False,
            "email_msgid": None,
            "emailed_at": None,
            "source": source,
            "ip": ip,
            "user_agent": user_agent,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.leads.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _lead_from_doc(doc)

    async def set_consent(self, lead_id: str, choice: str) -> None:
        await self.leads.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": {"send_details_choice": choice, "updated_at": _utcnow()}},
        )

    async def mark_emailed(self, lead_id: str, message_id: str) -> None:
        now = _utcnow()
        await self.leads.update_one(
            {"_id": ObjectId(lead_id)},
            {
                "$set": {
                    "emailed": True,
                    "email_msgid": message_id,
                    "emailed_at": now,
                    "updated_at": now,
                }
            },
        )

    async def get_lead(self, lead_id: str) -> Lead | None:
        doc = await self.leads.find_one({"_id": ObjectId(lead_id)})
        return _lead_from_doc(doc) if doc else None
