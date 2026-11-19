"""`/intake`: public endpoint website forms post leads to.

Authenticated by a campaign's website key in the ``X-Api-Key`` header (no session).
Browsers may call it from any site (CORS is opened for this path only); a key can
be limited to named sites. Body: JSON with the form's fields, e.g.
``{"name": "...", "phone": "...", "email": "...", "message": "..."}``; any other
field is kept as a form answer.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Request, status

from app.core.database import DbSession
from app.schemas.campaigns import IntakeAccepted
from app.schemas.common import ErrorResponse
from app.services import website_intake

router = APIRouter(prefix="/intake", tags=["intake"])


@router.post(
    "/leads",
    response_model=IntakeAccepted,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def receive_lead(
    request: Request,
    session: DbSession,
    payload: Annotated[dict[str, Any], Body()],
    x_api_key: Annotated[str | None, Header()] = None,
) -> IntakeAccepted:
    """Receive one website form submission into the key's campaign."""
    return await website_intake.receive(session, request, x_api_key, payload)
