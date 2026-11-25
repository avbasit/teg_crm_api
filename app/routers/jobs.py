"""`/jobs`: scheduled work, triggered by GitHub Actions (Vercel Hobby allows only daily cron).

Each call must send ``Authorization: Bearer <CRON_SECRET>``. GET is used so the
schedulers need no request body.
"""

import hmac
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.errors import AppError
from app.integrations.meta import MetaError
from app.services import meta_service

router = APIRouter(prefix="/jobs", tags=["jobs"], include_in_schema=False)
log = logging.getLogger(__name__)
UNAUTHORIZED = AppError(401, "unauthorized", "Missing or wrong job secret.")


def _check(authorization: str | None) -> None:
    secret = get_settings().cron_secret
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not secret or not given or not hmac.compare_digest(given, secret):
        raise UNAUTHORIZED


@router.get("/{job}")
async def run_job(
    job: Literal["leads", "spend"],
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """``leads``: catch missed leads (every 15 min). ``spend``: sync ad spend (daily)."""
    _check(authorization)
    results: dict[str, object] = {}
    for integration in await meta_service.connected_integrations(session):
        key = f"meta:{integration.organization_id}"
        try:
            if job == "leads":
                results[key] = await meta_service.sync_leads(session, integration)
            else:
                results[key] = {"spend_rows": await meta_service.sync_spend(session, integration)}
        except MetaError as exc:  # one organisation's failure doesn't stop the others
            log.warning("job %s failed for %s: %s", job, key, exc)
            results[key] = {"error": str(exc)}
    return {"job": job, "results": results}
