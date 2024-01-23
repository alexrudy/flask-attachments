import os
from pathlib import Path

import click.testing
import pytest
from flask import Flask
from flask_attachments.cli import group
from flask_attachments.compression import CompressionAlgorithm
from flask_attachments.extension import settings
from flask_attachments.models import Attachment
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.usefixtures("extension")


@pytest.fixture
def attachment(session: Session) -> Attachment:
    att = Attachment(filename="example.txt", content_type="text/plain")
    att.data(b"Hello from the test framework")
    session.add(att)
    session.commit()
    return att


def test_cli_import(tmp_path: Path, session: Session) -> None:
    filename = tmp_path / "example.txt"
    filename.write_text("Hello from the test framework")

    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["import", str(filename)], catch_exceptions=False)

    assert outcome.exit_code == 0

    attachment = session.scalar(select(Attachment).where(Attachment.filename == "example.txt"))
    assert attachment is not None
    assert attachment.filename == "example.txt"


def test_cli_import_arguments(tmp_path: Path, session: Session) -> None:
    filename = tmp_path / "example.txt"
    filename.write_text("Hello from the test framework")

    runner = click.testing.CliRunner()

    outcome = runner.invoke(
        group, ["import", str(filename), "--name", "test.txt", "--content-type", "text/plain"], catch_exceptions=False
    )
    assert outcome.exit_code == 0

    attachment = session.scalar(select(Attachment).where(Attachment.filename == "test.txt"))
    assert attachment is not None
    assert attachment.filename == "test.txt"
    assert attachment.content_type == "text/plain"


def test_cli_compression_digest_args(tmp_path: Path, session: Session) -> None:
    filename = tmp_path / "example.txt"
    filename.write_text("Hello from the test framework")

    runner = click.testing.CliRunner()

    outcome = runner.invoke(
        group, ["import", str(filename), "--compression", "gzip", "--digest", "sha256"], catch_exceptions=False
    )
    assert outcome.exit_code == 0

    attachment = session.scalar(select(Attachment).where(Attachment.filename == "example.txt"))
    assert attachment is not None
    assert attachment.filename == "example.txt"
    assert attachment.compression == CompressionAlgorithm.GZIP
    assert attachment.digest_algorithm == "sha256"


def test_cli_import_overwrite(tmp_path: Path, session: Session) -> None:
    filename = tmp_path / "example.txt"
    filename.write_text("Hello from the test framework")

    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["import", str(filename)], catch_exceptions=False)
    assert outcome.exit_code == 0

    attachment = session.scalar(select(Attachment).where(Attachment.filename == "example.txt"))
    assert attachment is not None
    assert attachment.filename == "example.txt"
    print(attachment.digest)

    filename.write_text("Hello again from the test framework")
    outcome = runner.invoke(group, ["import", str(filename)], catch_exceptions=False)
    assert outcome.exit_code == 0

    session.expire_all()
    attachment = session.scalar(select(Attachment).where(Attachment.filename == "example.txt"))
    assert attachment is not None
    assert attachment.filename == "example.txt"
    attachment.warm()
    assert attachment.cached_filepath.read_text() == "Hello from the test framework"
    print(attachment.digest)
    attachment.cached_filepath.unlink()

    outcome = runner.invoke(group, ["import", str(filename), "--overwrite"], catch_exceptions=False)
    assert outcome.exit_code == 0

    session.expire_all()
    attachment = session.scalar(select(Attachment).where(Attachment.filename == "example.txt"))
    assert attachment is not None
    assert attachment.filename == "example.txt"
    print(attachment.digest)
    attachment.warm()
    assert attachment.cached_filepath.read_text() == "Hello again from the test framework"


@pytest.mark.usefixtures("attachment")
def test_cli_list_table() -> None:
    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["list", "--rich"], catch_exceptions=False)

    assert outcome.exit_code == 0
    print(outcome.output)
    assert "exam" in outcome.output


@pytest.mark.usefixtures("attachment")
def test_cli_list_plain() -> None:
    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["list", "--no-rich"], catch_exceptions=False)
    assert outcome.exit_code == 0
    assert "example.txt" in outcome.output


@pytest.mark.usefixtures("attachment")
def test_cli_list_content_type() -> None:
    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["list", "--no-rich", "--content-type", "text/plain"], catch_exceptions=False)

    assert outcome.exit_code == 0
    assert "example.txt" in outcome.output

    outcome = runner.invoke(group, ["list", "--no-rich", "--content-type", "text/html"], catch_exceptions=False)

    assert outcome.exit_code == 0
    assert outcome.output == ""


def test_cli_list_ondisk(attachment: Attachment) -> None:
    runner = click.testing.CliRunner()

    attachment.cached_filepath.unlink(missing_ok=True)

    outcome = runner.invoke(group, ["warm", "--content-type", "text/html"], catch_exceptions=False)
    outcome = runner.invoke(group, ["list", "--no-rich", "--on-disk"], catch_exceptions=False)

    assert outcome.exit_code == 0
    assert outcome.output == ""

    outcome = runner.invoke(group, ["warm"], catch_exceptions=False)
    outcome = runner.invoke(group, ["list", "--no-rich", "--on-disk"], catch_exceptions=False)
    assert outcome.exit_code == 0
    assert "example.txt" in outcome.output


@pytest.mark.usefixtures("attachment")
def test_cli_delete(session: Session) -> None:
    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["delete", "--content-type", "text/html"], catch_exceptions=False)
    assert outcome.exit_code == 0

    assert session.scalar(select(func.count(Attachment.id))) == 1

    outcome = runner.invoke(group, ["delete"], catch_exceptions=False)
    assert outcome.exit_code == 0
    assert session.scalar(select(func.count(Attachment.id))) == 0


@pytest.mark.usefixtures("attachment")
def test_cli_warm(session: Session, app: Flask) -> None:
    runner = click.testing.CliRunner()

    outcome = runner.invoke(group, ["warm", "--content-type", "text/html"], catch_exceptions=False)
    assert outcome.exit_code == 0

    assert session.scalar(select(func.count(Attachment.id))) == 1
    with app.app_context():
        assert len(settings.cache()) == 0

    outcome = runner.invoke(group, ["warm", "--content-type", "text/plain"], catch_exceptions=False)
    assert outcome.exit_code == 0
    assert session.scalar(select(func.count(Attachment.id))) == 1
    with app.app_context():
        assert len(settings.cache()) == 1


@pytest.mark.usefixtures("app_context")
def test_cli_prune(session: Session, attachment: Attachment) -> None:
    runner = click.testing.CliRunner()

    attachment.warm()
    os.utime(attachment.cached_filepath, (0, 0))
    assert len(settings.cache()) == 1

    outcome = runner.invoke(group, ["prune"], catch_exceptions=False)
    assert outcome.exit_code == 0

    assert session.scalar(select(func.count(Attachment.id))) == 1
    assert len(settings.cache()) == 0


@pytest.mark.usefixtures("app_context")
def test_cli_clear(session: Session, app: Flask, attachment: Attachment) -> None:
    runner = click.testing.CliRunner()

    attachment.warm()
    assert len(settings.cache()) == 1

    outcome = runner.invoke(group, ["clear"], catch_exceptions=False)
    assert outcome.exit_code == 0

    assert session.scalar(select(func.count(Attachment.id))) == 1
    assert len(settings.cache()) == 0
