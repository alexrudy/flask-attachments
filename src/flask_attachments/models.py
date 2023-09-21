import contextlib
import datetime as dt
import hashlib
import io
import mimetypes
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any
from typing import cast
from zlib import crc32

import structlog
from flask import send_file
from flask import url_for
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import LargeBinary
from sqlalchemy import String
from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import deferred
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import validates
from werkzeug import Response
from werkzeug.datastructures import FileStorage
from werkzeug.http import parse_options_header
from werkzeug.utils import cached_property

from .extension import CompressionAlgorithm
from .extension import settings

logger = structlog.get_logger(__name__)
mtdb = mimetypes.MimeTypes()


class Base(DeclarativeBase):
    """Provides a base class for all models in flask-attachments"""


class Attachment(Base):
    """Represents a file on the filesystem / or stored in the attachment database"""

    __tablename__ = "attachment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated: Mapped[dt.datetime] = mapped_column(DateTime, onupdate=func.now(), server_default=func.now())

    filename: Mapped[str] = mapped_column(String(), doc="for display and serving purposes")
    content_type: Mapped[str] = mapped_column(String(), doc="for serving the correct file content_type")
    content_length: Mapped[int] = mapped_column(Integer(), doc="uncompressed content length (bytes)")
    contents: Mapped[bytes] = deferred(mapped_column(LargeBinary(), doc="compressed file contents"))
    compression: Mapped[CompressionAlgorithm] = mapped_column(
        Enum(CompressionAlgorithm), nullable=False, doc="which compression alogirthm was used"
    )
    digest: Mapped[str] = mapped_column(String(), nullable=False, doc="hash digest for file")
    digest_algorithm: Mapped[str] = mapped_column(String(), nullable=False, doc="algorithm for digest")

    __table_args__ = ({"schema": "attachments"},)

    def __repr__(self) -> str:
        return f"<Attachment id={self.id} filename={self.filename} mimetype={self.mimetype}>"

    def _empty_cache(self, property: str) -> None:
        try:
            delattr(self, property)
        except (AttributeError, KeyError):
            pass

    @cached_property
    def _parsed_content_type(self) -> tuple[str, dict[str, str]]:
        return parse_options_header(self.content_type)

    @cached_property
    def mimetype(self) -> None | str:
        """Get the mimetype for this file"""
        if self.content_type is None:
            if self.filename is not None:
                return mtdb.guess_type(self.filename)[0]
            return None
        return self._parsed_content_type[0]

    @validates("content_type")
    def _validate_content_type(self, key: str, value: Any) -> Any:
        self._empty_cache("_parsed_content_type")

        (well_known_types, standard_types) = mtdb.types_map_inv
        mime_type = parse_options_header(value)[0]
        if mime_type not in well_known_types and mime_type not in standard_types:
            logger.warning(f"Unknown MIME type: {value}")

        return value

    @cached_property
    def extension(self) -> None | str:
        """Get the presumed extension for this file"""
        if self.filename is not None:
            suffix = Path(self.filename).suffix
            if suffix is not None:
                return suffix
        if self.mimetype is not None:
            return mtdb.guess_extension(self.mimetype, strict=False)
        return None

    @cached_property
    def cached_at(self) -> dt.datetime | None:
        """When this file was written to disk"""
        try:
            return dt.datetime.fromtimestamp(self.cached_filepath.stat().st_ctime)
        except FileNotFoundError:
            return None

    @cached_property
    def size(self) -> int | None:
        """When this file was written to disk"""
        try:
            return self.cached_filepath.stat().st_size
        except FileNotFoundError:
            return self.content_length

    @cached_property
    def compressed_size(self) -> int:
        """When this file was written to disk"""
        return len(self.contents)

    @cached_property
    def etag(self) -> str:
        """The entity tag which will uniquely correspond to this file"""
        source = f"{self.digest_algorithm}-{self.digest}-{self.filename}-{self.content_type}"
        return f"{self.id.hex}-{crc32(source.encode('utf-8')):x}"

    @cached_property
    def link(self) -> str:
        return url_for("attachments.id", id=self.id)

    @cached_property
    def download_link(self) -> str:
        return url_for("attachments.download", id=self.id)

    @classmethod
    def from_file(
        cls,
        file: os.PathLike[str],
        content_type: str | None = None,
        compression: CompressionAlgorithm | str | None = None,
        digest_algorithm: str | None = None,
    ) -> "Attachment":
        """Import a file from the filesystem"""

        if content_type is None:
            content_type = mtdb.guess_type(str(file))[0]

        attachment = cls(
            filename=Path(file).name,
            content_type=content_type,
        )

        with open(file, "rb") as f:
            data = f.read()

        attachment.data(data, compression=compression, digest_algorithm=digest_algorithm)
        return attachment

    def data(
        self, data: bytes, compression: CompressionAlgorithm | str | None = None, digest_algorithm: str | None = None
    ) -> None:
        """Import a file from bytes"""
        if compression is None:
            compression = cast(CompressionAlgorithm, settings.compression())  # type: ignore[attr-defined]
        elif isinstance(compression, str):
            compression = CompressionAlgorithm[compression.upper()]

        if digest_algorithm is None:
            digest_algorithm = cast(str, settings.digest())  # type: ignore[attr-defined]

        # Save file contents
        self.content_length = len(data)
        self.contents = compressed = compression.compress(data)
        self.compression = compression

        # Compute Digest
        self.digest = hashlib.new(digest_algorithm, compressed).hexdigest()
        self.digest_algorithm = digest_algorithm

    def receive(self, file: FileStorage) -> None:
        """Receive an uploaded file, compressing and saving it as appropritate"""

        # Set metadata if not set already
        if self.filename is None:
            self.filename = file.filename

        if self.content_type is None:
            self.content_type = file.content_type

        # Compress Data
        buffer = io.BytesIO()

        with contextlib.closing(buffer):
            file.save(buffer)
            self.data(buffer.getvalue())

    @cached_property
    def cached_filepath(self) -> Path:
        filename = cast(Path, settings.cache_directory()) / f"{self.digest_algorithm}-{self.digest}"  # type: ignore[attr-defined]
        if self.extension is not None:
            return filename.with_suffix(self.extension)
        return filename

    def warm(self) -> None:
        """Ensure that the file exists in the cache."""

        compression = self.compression

        with tempfile.TemporaryDirectory() as directory:
            bufferfile = Path(directory) / self.cached_filepath.name
            with bufferfile.open("w+b") as file:
                file.write(compression.decompress(self.contents))

            try:
                bufferfile.rename(self.cached_filepath)
            except OSError as exc:
                if exc.errno == 18:
                    logger.warning(
                        "Detected cross-device attachment move, falling back to shutil",
                        src=bufferfile,
                        dst=self.cached_filepath,
                    )
                    shutil.move(bufferfile, self.cached_filepath)
                else:
                    logger.error(
                        "Error moving attachment to a temporary directory", src=bufferfile, dst=self.cached_filepath
                    )
                    raise

        self._empty_cache("cached_at")

    def send(self, as_download: bool = False) -> Response:
        """Send this attachment as a file to the client"""
        if not self.cached_filepath.exists():
            self.warm()
        else:
            self.cached_filepath.touch(exist_ok=True)

        if not self.cached_filepath.exists():
            logger.error(
                "The cache was just warmed, but the file does not exist", path=self.cached_filepath, id=self.id
            )

        return send_file(
            self.cached_filepath,
            mimetype=self.mimetype,
            last_modified=self.updated.timestamp(),
            download_name=self.filename,
            as_attachment=as_download,
            conditional=True,
            etag=self.etag,
        )

    def clear(self) -> None:
        """Remove the file from the cache"""
        self.cached_filepath.unlink(missing_ok=True)
