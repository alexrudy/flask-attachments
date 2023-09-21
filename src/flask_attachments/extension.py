import bz2
import contextlib
import dataclasses as dc
import datetime as dt
import enum
import gzip
import hashlib
import lzma
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import structlog
from flask import current_app
from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from werkzeug.local import LocalProxy


log = structlog.get_logger(__name__)

EXTENSION_NAME = "flask-attachments"
EXTENSION_CONFIG_NAMESPACE = "ATTACHMENTS_"


class CompressionAlgorithm(enum.Enum):
    NONE = enum.auto()
    GZIP = enum.auto()
    BZ2 = enum.auto()
    LZMA = enum.auto()

    def compress(self, data: bytes) -> bytes:
        if self == CompressionAlgorithm.NONE:
            return data
        if self == CompressionAlgorithm.LZMA:
            return lzma.compress(data)
        if self == CompressionAlgorithm.BZ2:
            return bz2.compress(data)
        if self == CompressionAlgorithm.GZIP:
            return gzip.compress(data)

        raise ValueError(f"Unsupported compression kind: {self.name}")

    def decompress(self, data: bytes) -> bytes:
        if self == CompressionAlgorithm.NONE:
            return data
        if self == CompressionAlgorithm.LZMA:
            return lzma.decompress(data)
        if self == CompressionAlgorithm.BZ2:
            return bz2.decompress(data)
        if self == CompressionAlgorithm.GZIP:
            return gzip.decompress(data)

        raise ValueError(f"Unsupported compression kind: {self.name}")


logger = structlog.get_logger(__name__)


@contextlib.contextmanager
def suppress(msg: str) -> Iterator[None]:
    try:
        yield
    except BaseException as ex:
        logger.exception(f"Exception while {msg}: {ex!r}")


@dc.dataclass
class AttachmentSettings:
    engine: Engine

    @property
    def config(self) -> dict[str, Any]:
        return current_app.config.get_namespace(EXTENSION_CONFIG_NAMESPACE, lowercase=False)

    def attach_filepath(self) -> str | None:
        uri = make_url(self.config["DATABASE_URI"])
        if "sqlalchemy" in current_app.extensions:
            db = current_app.extensions["sqlalchemy"].db
            (uri, _options) = db.apply_driver_hacks(current_app, uri, {})
        return uri.database

    def attach_ddl(self) -> str:
        schema = self.config.get("DATABASE_SCHEMA", "attachments")
        return f'ATTACH DATABASE "{self.attach_filepath()}" AS {schema}'  # noqa: B907

    def digest(self) -> str:
        return self.config["DIGEST"]

    def compression(self) -> CompressionAlgorithm:
        compression = self.config["COMPRESSION"]
        return CompressionAlgorithm[compression.upper()]

    def cache_directory(self) -> Path:
        return (Path(current_app.instance_path) / Path(self.config["CACHE_DIRECTORY"])).absolute()

    def cache_age(self) -> dt.timedelta:
        return dt.timedelta(hours=self.config["CACHE_AGE_HOURS"])

    def cache_size(self) -> int:
        return self.config["CACHE_SIZE_MAX"]


def get_settings() -> AttachmentSettings:
    return current_app.extensions[EXTENSION_NAME]


settings = LocalProxy(get_settings)


class AttachmentsConfigurationError(ValueError):
    pass


class Attachments:
    def __init__(self, app: Flask | None = None) -> None:
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Initialize the app here"""

        from .cli import group as command_group

        if f"{EXTENSION_CONFIG_NAMESPACE}DATABASE_URI" not in app.config:
            raise AttachmentsConfigurationError(
                f"Must set {EXTENSION_CONFIG_NAMESPACE}DATABASE_URI for attachments extension"
            )

        app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}DATABASE_SCHEMA", "attachments")

        directory = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}CACHE_DIRECTORY", None)
        if directory is None:
            app.config[f"{EXTENSION_CONFIG_NAMESPACE}CACHE_DIRECTORY"] = directory = tempfile.mkdtemp()
            log.warn("Using a temporary directory for attachment caching", directory=directory)

        # Ensure the directory exists
        directory = Path(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise AttachmentsConfigurationError(f"Unsupported attachment cache directory: {directory}") from ex

        cache_size = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}CACHE_SIZE_MAX", 2 * 10**9)
        try:
            int(cache_size)
        except ValueError as ex:
            raise AttachmentsConfigurationError(f"Invalid cache size: {cache_size}") from ex

        cache_age = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}CACHE_AGE_HOURS", 12)
        try:
            int(cache_age)
        except ValueError as ex:
            raise AttachmentsConfigurationError(f"Invalid cache age: {cache_age}") from ex

        compression = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}COMPRESSION", "lzma")
        algorithm = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}DIGEST", "sha256")

        try:
            CompressionAlgorithm[compression.upper()]
        except KeyError as ex:
            raise AttachmentsConfigurationError(f"Unsupported compression algorithm: {compression}") from ex

        try:
            hashlib.new(algorithm)
        except ValueError as ex:
            raise AttachmentsConfigurationError(f"Unsupported digest algorithm: {algorithm}") from ex

        engine = create_engine(app.config[f"{EXTENSION_CONFIG_NAMESPACE}DATABASE_URI"])
        app.extensions[EXTENSION_NAME] = AttachmentSettings(engine=engine)
        app.cli.add_command(command_group)

        if app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}BLUEPRINT", True):
            from .views import bp

            app.register_blueprint(bp)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    ddl = current_app.extensions[EXTENSION_NAME].attach_ddl()
    log.debug("Attaching database", ddl=ddl)
    cursor.execute(ddl)
    cursor.close()
