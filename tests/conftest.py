import logging
from collections.abc import Iterator
from typing import Any
from typing import cast

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_attachments.extension import Attachments
from flask_attachments.extension import settings
from flask_attachments.models import Base
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True, scope="session")
def log_engine_queries() -> None:
    logger = logging.getLogger(__name__)

    def log_queries(
        conn: Any, cursor: Any, statement: str, parameters: dict[str, Any], context: Any, executemany: Any
    ) -> None:
        logger.debug("%s parameters=%r", statement, parameters)

    event.listen(Engine, "before_cursor_execute", log_queries)


@pytest.fixture()
def engine(app_context: None, extension: Attachments) -> Engine:
    return cast(Engine, settings.engine)  # type: ignore[attr-defined]


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
    app.config["SERVER_NAME"] = "localhost"

    yield app


@pytest.fixture
def app_context(app: Flask) -> Iterator[None]:
    with app.app_context():
        yield None


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def configure_structlog() -> None:
    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            # Transform event dict into `logging.Logger` method arguments.
            # "event" becomes "msg" and the rest is passed as a dict in
            # "extra". IMPORTANT: This means that the standard library MUST
            # render "extra" for the context to appear in log entries! See
            # warning below.
            structlog.stdlib.render_to_log_kwargs,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
