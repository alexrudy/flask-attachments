import bz2
import enum
import gzip
import hashlib
import io
import lzma
from typing import cast
from typing import IO
from typing import Protocol


class CompressionAlgorithm(enum.Enum):
    NONE = enum.auto()
    GZIP = enum.auto()
    BZ2 = enum.auto()
    LZMA = enum.auto()

    def compress(self, data: bytes) -> bytes:
        """Compress in place the given data."""
        if self == CompressionAlgorithm.NONE:
            return data
        if self == CompressionAlgorithm.LZMA:
            return lzma.compress(data)
        if self == CompressionAlgorithm.BZ2:
            return bz2.compress(data)
        if self == CompressionAlgorithm.GZIP:  # pragma: no branch
            return gzip.compress(data)

    def open(self, stream: IO[bytes], mode: str) -> IO[bytes]:
        """Open the given stream in the given mode, returning a compressed stream or file-like object."""
        if self == CompressionAlgorithm.NONE:
            return stream

        if "b" not in mode:
            mode = mode + "b"

        if self == CompressionAlgorithm.LZMA:
            return cast(IO[bytes], lzma.open(stream, mode=mode))
        if self == CompressionAlgorithm.BZ2:
            return cast(IO[bytes], bz2.open(stream, mode=mode))
        if self == CompressionAlgorithm.GZIP:  # pragma: no branch
            return cast(IO[bytes], gzip.open(stream, mode=mode))

    def stream(self, digest: str) -> "CompressingStream":
        return CompressingStream(self, digest=digest)

    def read(self, stream: IO[bytes]) -> "DecompressingStream":
        return DecompressingStream(self, stream)

    def decompress(self, data: bytes) -> bytes:
        """Decompress in place the given data."""
        if self == CompressionAlgorithm.NONE:
            return data
        if self == CompressionAlgorithm.LZMA:
            return lzma.decompress(data)
        if self == CompressionAlgorithm.BZ2:
            return bz2.decompress(data)
        if self == CompressionAlgorithm.GZIP:  # pragma: no branch
            return gzip.decompress(data)


class Hash(Protocol):
    """Matches the interface of hashlib.hashlib._Hash"""

    def update(self, data: bytes) -> None:
        ...

    def hexdigest(self) -> str:
        ...


class CompressingStream(io.BufferedIOBase):
    """A stream which compresses, digests, and tracks the length of the data written to it.

    This prevents having to do all of these things independently, and allows us to compress without
    holding a full file in memory, even if we do hold the compressed data in memory.
    """

    def __init__(self, algorithm: CompressionAlgorithm, digest: str) -> None:
        super().__init__()
        self.algorithm = algorithm
        self.inner = io.BytesIO()
        self.stream = self.algorithm.open(self.inner, "wb")
        self.length = 0
        self.digest = hashlib.new(digest)

    def write(self, data: bytes | bytearray) -> int:  # type: ignore[override]
        self.length += len(data)
        self.digest.update(data)
        return self.stream.write(data)

    def getvalue(self) -> bytes:
        return self.inner.getvalue()

    def hexdigest(self) -> str:
        return self.digest.hexdigest()

    def close(self) -> None:
        if self.algorithm != CompressionAlgorithm.NONE:
            self.stream.close()


class DecompressingStream(io.BufferedIOBase):
    def __init__(self, algorithm: CompressionAlgorithm, stream: IO[bytes]) -> None:
        super().__init__()
        self.algorithm = algorithm
        self.stream = self.algorithm.open(stream, "rb")

    def read(self, size: int | None = -1) -> bytes:
        if size is None:  # pragma: no cover
            return self.stream.read()
        return self.stream.read(size)

    def close(self) -> None:
        if self.algorithm != CompressionAlgorithm.NONE:
            self.stream.close()
