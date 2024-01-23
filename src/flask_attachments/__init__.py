"""Top-level package for Flask Attachments."""
from .compression import CompressionAlgorithm
from .extension import Attachments
from .models import Attachment
from .services import AttachmentCache

__author__ = """Alex Rudy"""
__email__ = "opensource@alexrudy.net"
__version__ = "0.1.0"


__all__ = ["Attachments", "CompressionAlgorithm", "Attachment", "AttachmentCache"]
