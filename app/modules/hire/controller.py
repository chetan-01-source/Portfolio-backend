"""Hire-flow HTTP controller. Thin layer:

- Validates request body (Pydantic does most of it)
- Pulls request metadata (IP, UA) from middleware-populated request.state
- Delegates to HireService
- Shapes the HTTP response
"""

from fastapi import APIRouter, HTTPException, Request

from app.deps import DbDep, RequestMetaDep, SettingsDep
from app.modules.hire.email_client import EmailClient
from app.modules.hire.repository import HireRepository
from app.modules.hire.schemas import (
    HireAnswerRequest,
    HireDoneResponse,
    HireQuestionResponse,
    HireStartRequest,
)
from app.modules.hire.service import (
    HireService,
    HoneypotTriggered,
    SessionNotFound,
)

router = APIRouter(prefix="/hire", tags=["hire"])


def _build_service(db, settings) -> HireService:
    return HireService(
        repo=HireRepository(db),
        email_client=EmailClient(settings),
        settings=settings,
    )


@router.post("/start", response_model=HireQuestionResponse)
async def start(
    body: HireStartRequest,
    request: Request,
    meta: RequestMetaDep,
    db: DbDep,
    settings: SettingsDep,
) -> HireQuestionResponse:
    # Manual rate-limit hook: slowapi decorators don't compose well with our DI signature,
    # so the global limiter (via app.state.limiter) can be applied here in a follow-up if needed.
    service = _build_service(db, settings)
    try:
        return await service.start(
            source=body.source,
            ip=meta.ip,
            user_agent=meta.user_agent,
            honeypot=body.website,
        )
    except HoneypotTriggered:
        raise HTTPException(status_code=400, detail="invalid request")


@router.post(
    "/answer",
    response_model=HireQuestionResponse | HireDoneResponse,
    response_model_exclude_none=True,
)
async def answer(
    body: HireAnswerRequest,
    meta: RequestMetaDep,
    db: DbDep,
    settings: SettingsDep,
):
    service = _build_service(db, settings)
    try:
        result = await service.handle_answer(
            session_id=body.session_id,
            answer=body.answer,
            ip=meta.ip,
            user_agent=meta.user_agent,
            honeypot=body.website,
        )
    except HoneypotTriggered:
        raise HTTPException(status_code=400, detail="invalid request")
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return result
