import pytest
from flask.testing import FlaskClient
from flask_attachments.models import Attachment
from sqlalchemy.orm import Session


@pytest.fixture
def attachment(session: Session) -> Attachment:
    att = Attachment(filename="example.txt", content_type="text/plain")
    att.data(b"Hello from the test framework")
    session.add(att)
    session.commit()
    return att


def test_download(client: FlaskClient, attachment: Attachment) -> None:
    with client.get(f"/attachments/download/{attachment.id!s}/") as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.text == "Hello from the test framework"  # type: ignore[attr-defined]


def test_get(client: FlaskClient, attachment: Attachment) -> None:
    with client.get(f"/attachments/id/{attachment.id!s}/") as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.text == "Hello from the test framework"  # type: ignore[attr-defined]


def test_etag_stable(client: FlaskClient, attachment: Attachment, session: Session) -> None:
    with client.get(f"/attachments/id/{attachment.id!s}/") as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.text == "Hello from the test framework"
        etag = resp.headers["ETag"]

    with client.get(f"/attachments/id/{attachment.id!s}/", headers={"If-None-Match": etag}) as resp:
        assert resp.status_code == 304
        assert etag == resp.headers["ETag"]

    attachment.data(b"Hello from the test framework, again")
    session.commit()

    with client.get(f"/attachments/id/{attachment.id!s}/", headers={"If-None-Match": etag}) as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.text == "Hello from the test framework, again"
        assert etag != resp.headers["ETag"]
        next_etag = resp.headers["ETag"]

    attachment.data(b"Hello from the test framework")
    session.commit()

    with client.get(f"/attachments/id/{attachment.id!s}/", headers={"If-None-Match": next_etag}) as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.text == "Hello from the test framework"
        assert next_etag != resp.headers["ETag"]
        assert etag == resp.headers["ETag"]
