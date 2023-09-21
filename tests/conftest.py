import logging
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_attachments.extension import Attachments
from flask_attachments.extension import settings
from flask_attachments.models import Base
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def log_queries(conn, cursor, statement, parameters, context, executemany) -> None:
    logger.debug("%s parameters=%r", statement, parameters)


@pytest.fixture()
def engine(app_context: None, extension: Attachments) -> Engine:
    event.listen(Engine, "before_cursor_execute", log_queries)
    return settings.engine


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def extension(app: Flask) -> Iterator[Attachments]:
    attachments = Attachments(app=app)
    yield attachments


@pytest.fixture
def app() -> Iterator[Flask]:
    app = Flask(__name__)
    app.config["ATTACHMENTS_DATABASE_URI"] = "sqlite:///:memory:"

    return app


@pytest.fixture
def app_context(app: Flask) -> Iterator[None]:
    with app.app_context():
        yield None


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    with app.test_client() as client:
        yield client
