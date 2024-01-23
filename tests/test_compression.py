import io

import pytest
from flask_attachments.compression import CompressionAlgorithm


@pytest.mark.parametrize("compression", CompressionAlgorithm)
def test_streamed(compression: CompressionAlgorithm) -> None:
    stream = compression.stream(digest="sha256")
    stream.write(b"Hello from the test framework")
    stream.close()

    compressed = stream.getvalue()

    assert compression.decompress(compressed) == b"Hello from the test framework"


@pytest.mark.parametrize("compression", CompressionAlgorithm)
def test_compressed(compression: CompressionAlgorithm) -> None:
    compressed = compression.compress(b"Hello from the test framework")

    assert compression.decompress(compressed) == b"Hello from the test framework"


@pytest.mark.parametrize("compression", CompressionAlgorithm)
def test_compressed_open(compression: CompressionAlgorithm) -> None:
    compressed = compression.compress(b"Hello from the test framework")

    with compression.open(io.BytesIO(compressed), mode="r") as stream:
        assert stream.read() == b"Hello from the test framework"

    inner = io.BytesIO(compressed)
    with compression.read(inner) as stream:
        assert stream.read() == b"Hello from the test framework"
