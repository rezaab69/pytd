"""Ergonomics over the generated API: files, text, paging, common sends."""

from __future__ import annotations

from tdpy.helpers.files import (
    FileTransferError,
    ProgressCallback,
    download,
    local_file,
    upload,
)
from tdpy.helpers.messages import reply_text, send_text
from tdpy.helpers.paging import MAX_BATCH, iter_history, iter_search
from tdpy.helpers.text import HTML, MARKDOWN, MARKDOWN_V1, ParseMode, parse, text_content

__all__ = [
    "HTML",
    "MARKDOWN",
    "MARKDOWN_V1",
    "MAX_BATCH",
    "FileTransferError",
    "ParseMode",
    "ProgressCallback",
    "download",
    "iter_history",
    "iter_search",
    "local_file",
    "parse",
    "reply_text",
    "send_text",
    "text_content",
    "upload",
]
