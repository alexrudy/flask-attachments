from typing import Iterator
import logging

import pytest
import structlog
from rich.traceback import install
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy import event
from flask import Flask
from flask.testing import FlaskClient

from flask_attachments.models import Base
from flask_attachments.extension import Attachments, settings

logger = structlog.get_logger(__name__)


def log_queries(conn, cursor, statement, parameters, context, executemany) -> None:
    logger.debug(statement, parameters=parameters)


@pytest.fixture(scope="session")
def setup():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # formatter = structlog.stdlib.ProcessorFormatter(
    #     processors=[structlog.dev.ConsoleRenderer()],
    # )

    install(show_locals=True)


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
