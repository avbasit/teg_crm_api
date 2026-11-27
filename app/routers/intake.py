"""`/intake`: public endpoint website forms post leads to.

Authenticated by a campaign's website key (no session), sent either as the
``X-Api-Key`` header or as ``?key=`` in the URL (for form plugins such as Elementor's
Webhook action, which can't set headers). Bodies:

- JSON: ``{"name": "...", "phone": "...", "email": "...", "message": "..."}``.
- Form-encoded (``application/x-www-form-urlencoded``): Elementor's simple or
  advanced webhook data. Fields are matched by their label.

Any field TEG CRM doesn't map to a lead column is kept as a form answer. Browsers may
call this endpoint from any site (CORS is opened for this path only); a key can be
limited to named sites.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.database import DbSession
from app.core.errors import invalid
from app.schemas.campaigns import IntakeAccepted
from app.schemas.common import ErrorResponse
from app.services import website_intake

router = APIRouter(prefix="/intake", tags=["intake"])
MAX_BODY = 256 * 1024


@router.post(
    "/leads",
    response_model=IntakeAccepted,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": IntakeAccepted,
            "description": "Received (form-encoded posts, e.g. Elementor)",
        },
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"schema": {"type": "object", "additionalProperties": True}},
                "application/x-www-form-urlencoded": {
                    "schema": {"type": "object", "additionalProperties": True}
                },
            }
        }
    },
)
async def receive_lead(
    request: Request,
    response: Response,
    session: DbSession,
    x_api_key: Annotated[str | None, Header()] = None,
    key: Annotated[
        str | None, Query(description="Website key, for plugins that can't send headers")
    ] = None,
) -> IntakeAccepted:
    """Receive one website form submission into the key's campaign."""
    body = await request.body()
    if len(body) > MAX_BODY:
        raise invalid("too_large", "The submission is too large.")
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        payload: dict[str, Any] = website_intake.flatten_form(body)
        response.status_code = status.HTTP_200_OK  # Elementor treats anything but 200 as a failure
    else:
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise invalid("invalid_json", "Send the form as JSON or as form data.") from exc
        if not isinstance(payload, dict):
            raise invalid("invalid_json", "Send the form fields as a JSON object.")
    return await website_intake.receive(session, request, x_api_key or key, payload)
