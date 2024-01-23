from unittest import mock

import pytest
from flask import Flask
from flask_attachments.extension import Attachments
from flask_attachments.extension import AttachmentsConfigurationError
from flask_attachments.extension import get_settings
from sqlalchemy import text
from sqlalchemy.engine import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import registry as Registry


def test_settings_without_app_context() -> None:
    with pytest.raises(RuntimeError):
        get_settings()


@pytest.mark.usefixtures("app_context")
def test_settings_without_init_app() -> None:
    with pytest.raises(RuntimeError):
        get_settings()


@pytest.mark.usefixtures("app_context", "extension")
def test_settings_sqlalchemy(app: Flask) -> None:
    settings = get_settings()
    app.extensions["sqlalchemy"] = sqlalchemy = mock.Mock()

    sqlalchemy.db.apply_driver_hacks.return_value = (make_url("sqlite:///:memory:"), {})

    assert settings.attach_filepath() == ":memory:"


def test_init_extension_without_app_context(registry: Registry) -> None:
    attachments = Attachments(registry=registry)

    assert repr(attachments).startswith("<Attachments")


missing = object()


@pytest.mark.parametrize(
    "key, value ,execption",
    [
        ("DIGEST", missing, None),
        ("DIGEST", None, TypeError),
        ("DIGEST", "", ValueError),
        ("DIGEST", "sha256", None),
        ("DIGEST", "sha512", None),
        ("COMPRESSION", missing, None),
        ("COMPRESSION", None, AttributeError),
        ("COMPRESSION", "", ValueError),
        ("COMPRESSION", "bz2", None),
        ("COMPRESSION", "lzma", None),
        ("COMPRESSION", "gzip", None),
        ("CACHE_DIRECTORY", missing, None),
        ("CACHE_DIRECTORY", None, None),
        ("CACHE_DIRECTORY", "", ValueError),
        ("CACHE_DIRECTORY", "cache", None),
        ("CACHE_AGE_HOURS", missing, None),
        ("CACHE_AGE_HOURS", None, TypeError),
        ("CACHE_AGE_HOURS", "", ValueError),
        ("CACHE_AGE_HOURS", "1", None),
        ("CACHE_SIZE_MAX", missing, None),
        ("CACHE_SIZE_MAX", None, TypeError),
        ("CACHE_SIZE_MAX", "", ValueError),
        ("CACHE_SIZE_MAX", "100", None),
        ("DATABASE_URI", missing, AttachmentsConfigurationError),
        ("DATABASE_URI", None, ArgumentError),
        ("DATABASE_URI", "", ArgumentError),
        ("DATABASE_URI", "sqlite:///:memory:", None),
        ("DATABASE_SCHEMA", missing, None),
        ("DATABASE_SCHEMA", None, None),
        ("DATABASE_SCHEMA", "", None),
        ("DATABASE_SCHEMA", "attachments", None),
    ],
)
def test_configuration_error(
    app: Flask,
    key: str,
    value: str | None | object,
    execption: type[Exception] | None,
    registry: Registry,
) -> None:
    key = f"ATTACHMENTS_{key}"
    if value is not missing:
        app.config[key] = value
    else:
        app.config.pop(key, None)

    if execption is None:
        Attachments(app, registry=registry)
    else:
        with pytest.raises(execption):
            Attachments(app, registry=registry)


def test_configuration_cache_directory_error(app: Flask, registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    app.config["ATTACHMENTS_CACHE_DIRECTORY"] = "/some/random/path"

    monkeypatch.setattr("pathlib.Path.mkdir", mock.Mock(side_effect=OSError))

    with pytest.raises(AttachmentsConfigurationError):
        Attachments(app, registry=registry)


def test_no_blueprint_registration(app: Flask, registry: Registry) -> None:
    app.config["ATTACHMENTS_BLUEPRINT"] = False

    Attachments(app, registry=registry)


def test_repeat_sqla_registry(app: Flask) -> None:
    with pytest.raises(AttachmentsConfigurationError):
        Attachments(app)


def test_engine_connect_early(app: Flask) -> None:
    url = make_url("sqlite:///:memory:")
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    engine = create_engine(url)
    with app.app_context():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
