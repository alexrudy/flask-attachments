import io
from inspect import isclass
from pathlib import Path

import pytest
from flask import Flask
from flask_attachments.compression import CompressionAlgorithm
from flask_attachments.models import Attachment
from flask_attachments.models import parse_compression
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage


pytestmark = pytest.mark.usefixtures("app_context")


@pytest.fixture
def attachment(session: Session) -> Attachment:
    att = Attachment(filename="example.txt", content_type="text/plain")
    att.data(b"Hello from the test framework")
    session.add(att)
    session.commit()
    return att


def test_attachment_repr(attachment: Attachment) -> None:
    assert repr(attachment).startswith("<Attachment")


@pytest.mark.usefixtures("extension")
@pytest.mark.parametrize(
    "algorithm,expected",
    [
        (None, CompressionAlgorithm.LZMA),
        ("", KeyError),
        ("none", CompressionAlgorithm.NONE),
        ("gzip", CompressionAlgorithm.GZIP),
        ("bz2", CompressionAlgorithm.BZ2),
        ("lzma", CompressionAlgorithm.LZMA),
        ("invalid", KeyError),
    ],
)
def test_parse_compression_algoritm(algorithm: str | None, expected: CompressionAlgorithm | type[Exception]) -> None:
    if isclass(expected) and issubclass(expected, Exception):
        with pytest.raises(expected):
            parse_compression(algorithm)
    else:
        assert parse_compression(algorithm) is expected


@pytest.mark.parametrize("compression", CompressionAlgorithm)
@pytest.mark.parametrize("digest", ["md5", "sha1", "sha256", "sha512"])
def test_options(compression: CompressionAlgorithm, digest: str) -> None:
    att = Attachment()

    att.data(b"Hello from the test framework", compression=compression, digest_algorithm=digest)

    att.compression = compression
    assert att.compression is compression

    att.digest = digest
    assert att.digest is digest


@pytest.mark.parametrize("compression", CompressionAlgorithm)
@pytest.mark.parametrize("digest", ["md5", "sha1", "sha256", "sha512"])
def test_streamed_options(compression: CompressionAlgorithm, digest: str) -> None:
    att = Attachment()

    data = io.BytesIO(b"Hello from the test framework")

    att.streamed(data, compression=compression, digest_algorithm=digest)

    att.compression = compression
    assert att.compression is compression

    att.digest = digest
    assert att.digest is digest


def test_infer_mimetype(session: Session) -> None:
    att = Attachment(filename="example.txt")
    att.data(b"Hello from the test framework")

    assert att.content_type is None
    assert att.mimetype == "text/plain"


def test_infer_mimetype_blank(session: Session) -> None:
    att = Attachment()
    att.data(b"Hello from the test framework")

    assert att.content_type is None
    assert att.mimetype is None


def test_invalid_content_type(session: Session) -> None:
    att = Attachment(filename="example.txt", content_type="text/plain")
    att.data(b"Hello from the test framework")

    assert att.content_type == "text/plain"
    assert att.mimetype == "text/plain"

    att.content_type = "invalid"
    assert att.content_type == "invalid"
    assert att.mimetype == "invalid"


def test_filename_blank(session: Session) -> None:
    att = Attachment()
    att.data(b"Hello from the test framework" * 100, compression=CompressionAlgorithm.GZIP)

    assert att.filename is None
    assert att.mimetype is None
    assert att.cached_at is None
    assert att.compressed_size == 71
    assert att.size == 29 * 100

    att.warm()
    assert att.cached_at is not None
    assert att.size == 29 * 100

    att.filename = "example.txt"
    assert att.extension == ".txt"
    assert att.filename == "example.txt"
    assert att.mimetype is None  # is still cached from earlier


@pytest.mark.usefixtures("extension")
@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("filename_type", [str, Path])
def test_from_file(tmp_path: Path, content_type: str | None, filename_type: type[str] | type[Path]) -> None:
    path = tmp_path / "example.txt"
    path.write_text("Hello from the test framework")

    att = Attachment.from_file(filename_type(path), content_type=content_type)
    assert att.filename == "example.txt"
    assert att.extension == ".txt"
    assert att.mimetype == "text/plain"
    assert att.size == 29
    assert att.cached_at is None

    att.warm()
    assert att.cached_at is not None
    assert att.size == 29


@pytest.mark.usefixtures("extension")
@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("filename", ["example.txt", None])
def test_recv_file(tmp_path: Path, filename: str | None, content_type: str | None) -> None:
    path = tmp_path / "example.txt"
    path.write_text("Hello from the test framework")

    with path.open("rb") as f:
        file = FileStorage(f)

        att = Attachment(filename=filename, content_type=content_type)
        att.receive(file)

    assert att.filename == "example.txt"
    assert att.extension == ".txt"
    assert att.mimetype == "text/plain"
    assert att.size == 29
    assert att.cached_at is None

    att.warm()
    assert att.cached_at is not None
    assert att.size == 29


@pytest.mark.usefixtures("extension")
@pytest.mark.parametrize(
    "filename, content_type, extension",
    [
        ("example.txt", None, ".txt"),
        (None, "text/plain", ".txt"),
        ("example", None, None),
        ("example", "text/plain", ".txt"),
    ],
)
def test_guess_extension(filename: str | None, content_type: str | None, extension: str) -> None:
    att = Attachment(filename=filename, content_type=content_type)
    assert att.extension == extension


@pytest.mark.usefixtures("extension")
def test_attachment_deleted_during_send(app: Flask, attachment: Attachment, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda _: False)

    with app.test_request_context():
        with attachment.send() as response:
            assert response.status_code == 200


@pytest.mark.usefixtures("extension", "app")
def test_cache_on_different_filesystem(attachment: Attachment, monkeypatch: pytest.MonkeyPatch) -> None:
    def _rename(self: Path, target: Path) -> bool:
        err = OSError("Invalid cross-device link")
        err.errno = 18
        raise err

    monkeypatch.setattr(Path, "rename", _rename)

    attachment.warm()


@pytest.mark.usefixtures("extension", "app")
def test_cache_broken_filesystem(attachment: Attachment, monkeypatch: pytest.MonkeyPatch) -> None:
    def _rename(self: Path, target: Path) -> bool:
        raise FileNotFoundError("No such file or directory")

    monkeypatch.setattr(Path, "rename", _rename)

    with pytest.raises(FileNotFoundError):
        attachment.warm()
