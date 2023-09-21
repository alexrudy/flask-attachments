import collections
import datetime as dt
from collections.abc import Iterator
from pathlib import Path

from .extension import AttachmentSettings
from .extension import get_settings


class AttachmentCache(collections.abc.Mapping[str, Path]):
    """On-disk cache for attachment files

    On-disk static files allows for more efficient serving.
    """

    def __init__(self, settings: AttachmentSettings | None = None) -> None:
        if settings is None:
            self.settings = get_settings()
        else:
            self.settings = settings

    def clear(self) -> None:
        """Empty the cache directory entirely"""
        for path in self.paths():
            path.unlink(missing_ok=True)

    def size(self) -> int:
        """Size of the cache, in bytes"""
        return sum(filename.stat().st_size for filename in self.paths())

    def paths(self) -> Iterator[Path]:
        """All the paths in this cache"""
        return self.settings.cache_directory().iterdir()

    def prune(self) -> None:
        """Prune the cache directory"""
        self._remove_expired()
        self._remove_oldest()

    def __getitem__(self, key: str) -> Path:
        return self.settings.cache_directory() / key

    def __delitem__(self, key: str) -> None:
        (self.settings.cache_directory() / key).unlink(missing_ok=True)

    def __iter__(self) -> Iterator[str]:
        for path in self.paths():
            yield path.name

    def __len__(self) -> int:
        return sum(1 for _ in self.paths())

    def _remove_expired(self) -> None:
        """Remove expired files"""
        now = dt.datetime.now()
        for path in self.paths():
            if dt.datetime.fromtimestamp(path.stat().st_mtime) + self.settings.cache_age() < now:
                path.unlink(missing_ok=True)

    def _remove_oldest(self) -> None:
        """Remove oldest files until we are below the cache threshold"""

        def _entry(path: Path) -> tuple[Path, int, float]:
            st = path.stat()
            return (path, st.st_size, st.st_mtime)

        # Get the list of sizes once
        entries = [_entry(path) for path in self.paths()]

        # Figure out how much to remove
        total = sum(size for _, size, _ in entries)
        excess = total - self.settings.cache_size()

        if excess <= 0:
            return

        # Sort them oldest -> youngest, largest -> smallest
        def _sortkey(elements: tuple[Path, int, float]) -> tuple[float, int]:
            _, size, mtime = elements
            return (mtime, -size)

        entries.sort(key=_sortkey)

        # Make a queue
        queue = collections.deque(entries)

        # Delete until nothing is left of we are smaller than the limit.
        while excess >= 0 and queue:
            path, size, _ = queue.popleft()

            path.unlink(missing_ok=True)
            excess -= size
