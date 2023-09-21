import contextlib
from collections.abc import Iterable
from pathlib import Path
from typing import IO
from typing import Iterator

import click
import humanize
import rich.console
import rich.table
from flask.cli import AppGroup
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Attachment
from .services import AttachmentCache
from .extension import settings

group = AppGroup("attach", help="Manage attachments")


@contextlib.contextmanager
def sqlalchemy_session() -> Iterator[Session]:
    """Create and use a session attached to an attachment database"""
    engine = settings.engine()
    with Session(engine) as session:
        yield session


@group.command(name="import")
@click.argument("files", type=click.File("r+b"), nargs=-1)
@click.option("-n", "--name", default=None, type=str, help="Set an explicit file name")
@click.option("-C", "--content-type", default=None, help="Add an explicit content type")
@click.option("-f", "--overwrite/--no-overwrite", default=False, help="Overwrite existing attachments")
@click.option("-c", "--compression", default=None, type=str, help="Compression algorithm")
@click.option("-d", "--digest", default=None, type=str, help="Digest algorithm")
def import_file(
    files: Iterable[IO[bytes]],
    name: str | None,
    content_type: str | None,
    overwrite: bool,
    compression: str | None,
    digest: str | None,
) -> None:
    """Import files into the attachment system"""
    with sqlalchemy_session() as session:
        for file in files:
            filename = Path(name or file.name).name
            existing_attachment = (
                session.execute(select(Attachment).where(Attachment.filename == filename)).scalars().first()
            )
            if existing_attachment is not None and (not overwrite):
                click.echo(f"Skipping {filename}")

            attachment = Attachment(filename=filename, content_type=content_type)
            attachment.data(file.read(), compression=compression, digest_algorithm=digest)
            session.add(attachment)
            session.commit()
            click.echo(f"Imported {filename}")


def show_attachement(attachment: Attachment, table: rich.table.Table | None = None) -> None:
    if attachment.cached_at is None:
        age_msg = "(not cached)"
    else:
        age_msg = humanize.naturaltime(attachment.cached_at)

    if attachment.size:
        ratio = f"{attachment.compressed_size / attachment.size:.1%}"
        size_msg = (
            f"{humanize.naturalsize(attachment.size)} -> {humanize.naturalsize(attachment.compressed_size)} {ratio}"
        )
    else:
        ratio = ""
        size_msg = f"??? -> {humanize.naturalsize(attachment.compressed_size)}"
    if table is None:
        click.echo(f"{attachment.id.hex} {attachment.filename:20s} {age_msg} {size_msg} {attachment.compression.name}")
    else:
        table.add_row(
            attachment.id.hex,
            attachment.filename,
            age_msg,
            humanize.naturalsize(attachment.size) if attachment.size else "??",
            humanize.naturalsize(attachment.compressed_size),
            ratio,
            attachment.compression.name,
        )


@group.command()
@click.option("-C", "--content-type", default=None, help="Filter by content type")
@click.option("--on-disk/--all", default=False, help="Show only on disk files")
@click.option("--rich/--no-rich", "use_rich", default=False, help="Use rich to pretty-print tables")
def list(content_type: str | None, on_disk: bool, use_rich: bool) -> None:
    """List all attachments files"""

    query = select(Attachment)

    if content_type:
        query = query.where(Attachment.content_type == content_type)

    if use_rich:
        table = rich.table.Table()
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Filename", justify="left", style="magenta", no_wrap=True)
        table.add_column("Age", justify="left", style="green", no_wrap=True)
        table.add_column("Size", justify="right", style="blue", no_wrap=True)
        table.add_column("Compressed", justify="right", style="cyan", no_wrap=True)
        table.add_column("Ratio", justify="right", style="yellow", no_wrap=True)
        table.add_column("Algo", justify="right", style="yellow", no_wrap=True)
    else:
        table = None

    with sqlalchemy_session() as session:
        for attachment in session.execute(query).scalars():
            if on_disk and not attachment.cached_filepath.exists():
                continue
            show_attachement(attachment, table=table)
        if table is not None:
            c = rich.console.Console()
            c.print(table)


@group.command()
@click.option("-C", "--content-type", default=None, help="Filter by content type")
def warm(content_type: str | None) -> None:
    """Warm the cache"""

    query = select(Attachment)

    if content_type:
        query = query.where(Attachment.content_type == content_type)

    with sqlalchemy_session() as session:
        for attachment in session.execute(query).scalars():
            attachment.warm()
            show_attachement(attachment)


@group.command(name="delete")
@click.option("-C", "--content-type", default=None, help="Filter by content type")
def delete_attachments(content_type: str | None) -> None:
    """Delete attachments"""

    query = delete(Attachment)

    if content_type:
        query = query.where(Attachment.content_type == content_type)

    with sqlalchemy_session() as session:
        session.execute(query)
        session.commit()


@group.command()
def prune() -> None:
    """Prune the cache"""
    cache = AttachmentCache()
    cache.prune()
    click.echo(f"The cache is now {humanize.naturalsize(cache.size())}")


@group.command()
def clear() -> None:
    """Clear the cache"""
    cache = AttachmentCache()
    cache.clear()
    click.echo(f"The cache is now {humanize.naturalsize(cache.size())}")
