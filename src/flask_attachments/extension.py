import contextlib
import dataclasses as dc
import datetime as dt
import hashlib
import tempfile
from pathlib import Path
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

import structlog
from flask import current_app
from flask import Flask
from sqlalchemy import event
from sqlalchemy import MetaData
from sqlalchemy.engine import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import registry as Registry
from werkzeug.local import LocalProxy

from .compression import CompressionAlgorithm

if TYPE_CHECKING:
    from .services import AttachmentCache


log = structlog.get_logger(__name__)

EXTENSION_NAME = "flask-attachments"
EXTENSION_CONFIG_NAMESPACE = "ATTACHMENTS_"


logger = structlog.get_logger(__name__)


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
        return dt.timedelta(hours=int(self.config["CACHE_AGE_HOURS"]))

    def cache_size(self) -> int:
        return int(self.config["CACHE_SIZE_MAX"])

    def cache(self) -> "AttachmentCache":
        from .services import AttachmentCache

        return AttachmentCache(self)


def get_settings() -> AttachmentSettings:
    try:
        return current_app.extensions[EXTENSION_NAME]
    except KeyError as exc:
        raise RuntimeError("No attachments extension registered on this app, call init_app first") from exc


settings = cast(AttachmentSettings, LocalProxy(get_settings))


class AttachmentsConfigurationError(ValueError):
    pass


class Attachments:
    def __init__(self, app: Flask | None = None, registry: Registry | None = None) -> None:
        from .models import Attachment

        if registry is None:
            registry = Registry()

        if not hasattr(Attachment, "__mapper__"):
            registry.map_declaratively(Attachment)
        elif Attachment.__table__ not in registry.metadata:  # type: ignore
            raise AttachmentsConfigurationError(
                "The Attachment model has already been mapped to a different metadata"
                "\nConsider providing a custom registry to the Attachments extension"
                "\nor only intializing the extension once"
            )

        self.registry = registry

        if app is not None:
            self.init_app(app)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} at {id(self)!s}>"

    @property
    def metadata(self) -> MetaData:
        return self.registry.metadata

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

        if not directory:
            raise AttachmentsConfigurationError(
                f"Must set {EXTENSION_CONFIG_NAMESPACE}CACHE_DIRECTORY for attachments extension"
            )

        # Ensure the directory exists
        directory = Path(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.exception("Failed to create attachment cache directory", directory=directory)
            raise AttachmentsConfigurationError(f"Unsupported attachment cache directory: {directory}") from exc

        cache_size = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}CACHE_SIZE_MAX", 2 * 10**9)
        try:
            int(cache_size)
        except ValueError as exc:
            log.exception("Invalid cache size", cache_size=cache_size)
            raise AttachmentsConfigurationError(f"Invalid cache size: {cache_size}") from exc

        cache_age = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}CACHE_AGE_HOURS", 12)
        try:
            int(cache_age)
        except ValueError as exc:
            log.exception("Invalid cache age", cache_age=cache_age)
            raise AttachmentsConfigurationError(f"Invalid cache age: {cache_age}") from exc

        compression = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}COMPRESSION", "lzma")
        algorithm = app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}DIGEST", "sha256")

        try:
            CompressionAlgorithm[compression.upper()]
        except KeyError as exc:
            log.exception("Unsupported compression algorithm", compression=compression)
            raise AttachmentsConfigurationError(f"Unsupported compression algorithm: {compression}") from exc

        try:
            hashlib.new(algorithm)
        except ValueError as exc:
            log.exception("Unsupported digest algorithm", algorithm=algorithm)
            raise AttachmentsConfigurationError(f"Unsupported digest algorithm: {algorithm}") from exc

        engine = create_engine(app.config[f"{EXTENSION_CONFIG_NAMESPACE}DATABASE_URI"])
        app.extensions[EXTENSION_NAME] = AttachmentSettings(engine=engine)
        app.cli.add_command(command_group)

        if app.config.setdefault(f"{EXTENSION_CONFIG_NAMESPACE}BLUEPRINT", True):
            from .views import bp

            app.register_blueprint(bp)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    try:
        ddl = current_app.extensions[EXTENSION_NAME].attach_ddl()
    except KeyError:
        log.debug("No attachments extension registered on this app, skipping")
        return
    except RuntimeError:
        log.debug("No app context, skipping")
        return

    log.debug("Attaching database", ddl=ddl)
    with contextlib.closing(dbapi_connection.cursor()) as cursor:
        cursor.execute(ddl)
