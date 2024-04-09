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
from typing import IO
from typing import TYPE_CHECKING
from zlib import crc32

import structlog
from flask import send_file
from flask import url_for
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import LargeBinary
from sqlalchemy import String
from sqlalchemy import Uuid
from sqlalchemy.orm import deferred
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import validates
from werkzeug import Response
from werkzeug.datastructures import FileStorage
from werkzeug.http import parse_options_header
from werkzeug.utils import cached_property

from .compression import CompressionAlgorithm
from .extension import settings

logger = structlog.get_logger(__name__)
mtdb = mimetypes.MimeTypes()

__all__ = ["Attachment"]


class Attachment:
    """Represents a file on the filesystem or stored in the attachment database"""

    __tablename__ = "attachment"

    #: Primary key (a UUID)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: When this attachment was created (in the database)
    created: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    #: When this attachment record   was last updated
    updated: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, onupdate=func.now(), server_default=func.now()
    )

    #: The name used for display and download of the attachment
    filename: Mapped[str] = mapped_column(String(), nullable=True, doc="for display and serving purposes")

    #: The MIME type of the file
    content_type: Mapped[str] = mapped_column(String(), nullable=True, doc="for serving the correct file content_type")

    #: The length of the file in bytes, uncompressed
    content_length: Mapped[int] = mapped_column(Integer(), nullable=True, doc="uncompressed content length (bytes)")

    #: The compressed file contents
    contents: Mapped[bytes] = deferred(mapped_column(LargeBinary(), doc="compressed file contents"))

    #: The compression algorithm used
    compression: Mapped[CompressionAlgorithm] = mapped_column(
        Enum(CompressionAlgorithm), nullable=False, doc="which compression alogirthm was used"
    )

    #: The hash digest of the file
    digest: Mapped[str] = mapped_column(String(), nullable=False, doc="hash digest for file")

    #: The hash digest algorithm used
    digest_algorithm: Mapped[str] = mapped_column(String(), nullable=False, doc="algorithm for digest")

    __table_args__ = ({"schema": "attachments"},)

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            filename: str | None = None,
            content_type: str | None = None,
            content_length: int | None = None,
            compression: CompressionAlgorithm | None = None,
            digest_algorithm: str | None = None,
        ) -> None:
            ...

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
        """The MIME type for this file, possibly inferred"""
        if self.content_type is None:
            if self.filename is not None:
                return mtdb.guess_type(self.filename)[0]
            return None
        return self._parsed_content_type[0]

    @validates("content_type")
    def _validate_content_type(self, key: str, value: Any) -> Any:
        self._empty_cache("_parsed_content_type")
        self._empty_cache("mimetype")

        (well_known_types, standard_types) = mtdb.types_map_inv
        mime_type = parse_options_header(value)[0]
        if mime_type not in well_known_types and mime_type not in standard_types:
            logger.warning(f"Unknown MIME type: {value}")

        return value

    @validates("filename")
    def _validate_filename(self, key: str, value: Any) -> Any:
        self._empty_cache("extension")
        self._empty_cache("etag")
        return value

    @cached_property
    def extension(self) -> None | str:
        """The presumed extension for this file"""
        if self.filename is not None:
            suffix = Path(self.filename).suffix
            if suffix:
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
        """Url for serving the file"""
        return url_for("attachments.id", id=self.id)

    @cached_property
    def download_link(self) -> str:
        """Url for downloading the file"""
        return url_for("attachments.download", id=self.id)

    @classmethod
    def from_file(
        cls,
        file: os.PathLike[str],
        content_type: str | None = None,
        compression: CompressionAlgorithm | str | None = None,
        digest_algorithm: str | None = None,
    ) -> "Attachment":
        """Create a new attachment by reading a file from disk"""

        if content_type is None:
            content_type = mtdb.guess_type(str(file))[0]

        attachment = cls(
            filename=Path(file).name,
            content_type=content_type,
        )

        with open(file, "rb") as stream:
            attachment.streamed(stream, compression=compression, digest_algorithm=digest_algorithm)
        return attachment

    def data(
        self, data: bytes, compression: CompressionAlgorithm | str | None = None, digest_algorithm: str | None = None
    ) -> None:
        """Load a file from bytes into this attachment"""
        compression = parse_compression(compression)
        digest_algorithm = parse_digest(digest_algorithm)

        # Save file contents
        self.content_length = len(data)
        self.contents = compressed = compression.compress(data)
        self.compression = compression

        # Compute Digest
        self.digest = hashlib.new(digest_algorithm, compressed).hexdigest()
        self.digest_algorithm = digest_algorithm

        self._empty_cache("etag")
        self._empty_cache("cached_at")
        self._empty_cache("size")
        self._empty_cache("compressed_size")

    def streamed(
        self,
        stream: IO[bytes],
        compression: CompressionAlgorithm | str | None = None,
        digest_algorithm: str | None = None,
        chunk_size: int = 16384,
    ) -> None:
        """Stream a file from bytes into this attachment"""
        compression = parse_compression(compression)
        digest_algorithm = parse_digest(digest_algorithm)

        # Save file contents
        contents = compression.stream(digest=digest_algorithm)
        with contextlib.closing(contents) as contents:
            shutil.copyfileobj(stream, contents, length=chunk_size)

        self.content_length = contents.length
        self.contents = contents.getvalue()
        self.compression = compression

        self.digest = contents.hexdigest()
        self.digest_algorithm = digest_algorithm

    def receive(self, file: FileStorage) -> None:
        """Receive an uploaded file, compressing and saving it as appropritate"""

        # Set metadata if not set already
        if self.filename is None:
            self.filename = Path(file.filename).name

        if self.content_type is None:
            self.content_type = file.content_type

        self.streamed(file.stream)

    @cached_property
    def cached_filepath(self) -> Path:
        """The path to the file in the cache (on disk)"""
        filename = settings.cache_directory() / f"{self.digest_algorithm}-{self.digest}"
        if self.extension is not None:
            return filename.with_suffix(self.extension)
        return filename

    def warm(self) -> None:
        """Ensure that the file exists in the cache."""

        compression = self.compression

        with tempfile.TemporaryDirectory() as directory:
            bufferfile = Path(directory) / self.cached_filepath.name
            with bufferfile.open("w+b") as file:
                buffer = io.BytesIO(self.contents)
                with compression.open(buffer, "rb") as compressed:
                    shutil.copyfileobj(compressed, file)

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
        self._empty_cache("size")

    def send(self, as_download: bool = False) -> Response:
        """Send this attachment as a file to the client"""
        if not self.cached_filepath.exists():
            self.warm()
        else:
            self.cached_filepath.touch(exist_ok=True)

        if not self.cached_filepath.exists():
            # log the error, but also move on to
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


def parse_compression(compression: CompressionAlgorithm | str | None) -> CompressionAlgorithm:
    if compression is None:
        return settings.compression()
    elif isinstance(compression, str):
        return CompressionAlgorithm[compression.upper()]
    return compression


def parse_digest(digest_algorithm: str | None) -> str:
    if digest_algorithm is None:
        return settings.digest()
    return digest_algorithm


@event.listens_for(Attachment, "load")
def clear_instance_cached_properties(instance: Attachment, context: Any) -> None:
    """Clear the cached properties on an instance of Attachment"""

    for name in dir(Attachment):
        if isinstance(getattr(Attachment, name, None), cached_property):
            instance._empty_cache(name)
