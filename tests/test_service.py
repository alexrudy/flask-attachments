import os
from collections.abc import Iterator

import pytest
from flask import Flask
from flask_attachments.extension import Attachments
from flask_attachments.extension import get_settings
from flask_attachments.models import Attachment
from flask_attachments.services import AttachmentCache
from sqlalchemy.orm import Session


pytestmark = pytest.mark.usefixtures("app_context")


@pytest.fixture
def attachment(session: Session) -> Attachment:
    att = Attachment(filename="example.txt", content_type="text/plain")
    att.data(b"Hello from the test framework")
    session.add(att)
    session.commit()
    return att


@pytest.fixture
def cache(extension: Attachments) -> Iterator[AttachmentCache]:
    yield AttachmentCache()


@pytest.mark.usefixtures("extension")
def test_cache_with_explicit_settings() -> None:
    settings = get_settings()

    cache = AttachmentCache(settings=settings)

    assert cache.settings is settings
    assert len(cache) == 0


def test_cache_empty(cache: AttachmentCache) -> None:
    assert len(cache) == 0

    assert "foo.txt" not in cache

    with pytest.raises(KeyError):
        cache["foo.txt"]


def test_cache_with_items(cache: AttachmentCache, attachment: Attachment) -> None:
    attachment.warm()
    key = attachment.cached_filepath.name

    path = cache[key]
    assert path.exists()

    assert len(cache) == 1
    assert key in cache

    items = list(cache)
    assert len(items) == 1
    assert items[0] == key

    del cache[key]
    assert not path.exists()


def test_cache_clear(cache: AttachmentCache, attachment: Attachment) -> None:
    attachment.warm()
    assert len(cache) == 1

    cache.clear()
    assert len(cache) == 0

    cache.clear()
    assert len(cache) == 0


def test_cache_expire_age(cache: AttachmentCache, attachment: Attachment) -> None:
    attachment.warm()
    assert len(cache) == 1

    cache.prune()
    assert len(cache) == 1

    os.utime(attachment.cached_filepath, (0, 0))
    cache.prune()
    assert len(cache) == 0


def test_cache_expire_size(cache: AttachmentCache, attachment: Attachment, app: Flask) -> None:
    attachment.warm()
    assert len(cache) == 1

    cache.prune()
    assert len(cache) == 1

    app.config["ATTACHMENTS_CACHE_SIZE_MAX"] = 0
    attachment.data(b"Hello from the test framework, again")
    attachment.warm()

    assert cache.size() > 0
    cache.prune()
    assert len(cache) == 0
