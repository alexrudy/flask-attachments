from uuid import UUID

import structlog
from flask import abort
from flask import Blueprint
from flask.typing import ResponseReturnValue as IntoResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extension import settings
from .models import Attachment

bp = Blueprint("attachments", __name__, url_prefix="/attachments/")
log = structlog.get_logger(__name__)


def _get_attachment_or_404(session: Session, id: UUID) -> Attachment:
    attachment = session.scalar(select(Attachment).where(Attachment.id == id))
    if attachment is None:
        abort(404)
    return attachment


@bp.route("/id/<uuid:id>/")
def id(id: UUID) -> IntoResponse:
    """Get a cached file by ID"""
    with Session(settings.engine) as session:  # type: ignore[attr-defined]
        attachment = _get_attachment_or_404(session, id)
        return attachment.send()


@bp.route("/download/<uuid:id>/")
def download(id: UUID) -> IntoResponse:
    """Download an attached file by ID"""
    with Session(settings.engine) as session:  # type: ignore[attr-defined]
        log.debug("Fetching file", id=id)
        attachment = _get_attachment_or_404(session, id)
        return attachment.send(as_download=True)
