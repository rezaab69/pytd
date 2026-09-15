"""Downloading and uploading, with progress.

TDLib does the transferring; these helpers do the waiting. Both directions
report progress through ``updateFile``, which is observed rather than consumed
so an application reading :meth:`~tdpy.core.client.Client.updates` still sees
every one.

The two directions are not symmetrical, because TDLib is not. ``downloadFile``
takes a ``synchronous`` flag and answers when the transfer is finished, failed
or cancelled -- a definitive signal worth using. Upload has no such flag, so
completion has to be read off the update stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeAlias

from tdpy.api import functions, types
from tdpy.api.base import convert, to_builtins
from tdpy.core.client import Client
from tdpy.exceptions import TdpyError

__all__ = [
    "FileTransferError",
    "ProgressCallback",
    "download",
    "local_file",
    "upload",
]

logger = logging.getLogger(__name__)

#: ``(transferred, total) -> None``. May be async. ``total`` is 0 while TDLib
#: still does not know the size, which happens early in a download.
ProgressCallback: TypeAlias = Callable[[int, int], Awaitable[None] | None]

_UPDATE_FILE = "updateFile"


class FileTransferError(TdpyError):
    """A transfer stopped without completing."""


def local_file(path: str | Path) -> types.InputFileLocal:
    """Reference a file on this machine, checking it is really there.

    TDLib reports a missing path as a generic 400 from whatever request carried
    it, several steps removed from the typo that caused it.

    Raises:
        FileNotFoundError: if ``path`` is not an existing file.
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"no such file: {resolved}")
    return types.InputFileLocal(path=str(resolved))


async def _report(progress: ProgressCallback | None, transferred: int, total: int) -> None:
    if progress is None:
        return
    try:
        result = progress(transferred, total)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        # A progress bar is never worth failing a transfer over.
        logger.exception("progress callback raised")


def _watch(
    client: Client,
    file_id: int | None,
    queue: asyncio.Queue[types.File],
) -> Callable[[], None]:
    """Feed ``updateFile`` payloads into ``queue``.

    ``file_id`` of ``None`` accepts every file, for the upload case where the
    id is not known until the request has already been answered -- subscribing
    afterwards would miss the updates that arrive in between.
    """

    def observe(payload: dict[str, Any]) -> None:
        if payload.get("@type") != _UPDATE_FILE:
            return
        file = payload.get("file") or {}
        if file_id is not None and file.get("id") != file_id:
            return
        queue.put_nowait(convert(file, types.File))

    return client.subscribe(observe)


async def _drain(queue: asyncio.Queue[types.File], progress: ProgressCallback | None) -> None:
    """Report progress until cancelled."""
    while True:
        file = await queue.get()
        transferred = file.local.downloaded_size or file.remote.uploaded_size
        await _report(progress, transferred, file.size or file.expected_size)


async def download(
    client: Client,
    file_id: int,
    *,
    priority: int = 1,
    offset: int = 0,
    limit: int = 0,
    progress: ProgressCallback | None = None,
    timeout: float | None = None,
) -> types.File:
    """Download a file and return it once complete.

    Args:
        client: A started client.
        file_id: From ``message.content`` -- for a photo, the ``id`` of the
            size you want.
        priority: 1 to 32; higher goes first.
        offset: Byte to start at.
        limit: Bytes to fetch from ``offset``. ``0`` means to the end.
        progress: Called with ``(downloaded, total)`` as the transfer runs.
        timeout: Give up waiting after this many seconds. The download itself
            keeps going inside TDLib; stop it with ``cancelDownloadFile``.

    Returns:
        The completed :class:`~tdpy.api.types.File`; ``file.local.path`` is
        where it landed.

    Raises:
        FileTransferError: if TDLib stopped without completing, which is what a
            cancellation looks like -- it is not an error to TDLib.
        TDLibError: if TDLib refused the request outright.
    """
    queue: asyncio.Queue[types.File] = asyncio.Queue()
    unsubscribe = _watch(client, file_id, queue) if progress is not None else None
    reporter = asyncio.create_task(_drain(queue, progress)) if progress is not None else None

    request = functions.DownloadFile(
        file_id=file_id,
        priority=priority,
        offset=offset,
        limit=limit,
        # TDLib answers only once the transfer has finished, failed or been
        # cancelled. That is a far better completion signal than guessing from
        # the update stream, where "no longer active" is ambiguous.
        synchronous=True,
    )
    try:
        payload = await client.invoke(to_builtins(request), timeout=timeout)
    finally:
        if unsubscribe is not None:
            unsubscribe()
        if reporter is not None:
            reporter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter

    file: types.File = convert(payload, types.File)
    if not file.local.is_downloading_completed:
        raise FileTransferError(
            f"download of file {file_id} stopped at "
            f"{file.local.downloaded_size}/{file.size or file.expected_size} bytes"
        )
    await _report(progress, file.local.downloaded_size, file.size or file.expected_size)
    return file


async def upload(
    client: Client,
    file: str | Path | types.InputFile,
    *,
    file_type: types.FileType | None = None,
    priority: int = 1,
    progress: ProgressCallback | None = None,
    timeout: float | None = None,
) -> types.File:
    """Upload a file ahead of sending it, and wait for it to finish.

    Most of the time this is unnecessary: handing :func:`local_file` straight
    to ``sendMessage`` lets TDLib upload as part of sending. Reach for this
    when the upload has to happen *before* the send -- TDLib's own example is a
    voice note still being recorded -- or when you want progress before the
    message exists.

    Args:
        client: A started client.
        file: A path, or an already-built :class:`~tdpy.api.types.InputFile`.
        file_type: What kind of file it is, which decides where TDLib files it.
        priority: 1 to 32; higher goes first.
        progress: Called with ``(uploaded, total)`` as the transfer runs.
        timeout: Give up waiting after this many seconds.

    Raises:
        FileTransferError: if the upload stopped without completing, or if
            ``timeout`` passed first.
        FileNotFoundError: if ``file`` is a path that does not exist.
    """
    source = file if isinstance(file, types.InputFile) else local_file(file)
    request = functions.PreliminaryUploadFile(file=source, file_type=file_type, priority=priority)

    # No `synchronous` flag exists for uploads, so completion has to be read off
    # the update stream -- and TDLib starts sending those before it answers the
    # request. Subscribing first, and filtering once the id is known, is the
    # only way not to miss the early ones.
    queue: asyncio.Queue[types.File] = asyncio.Queue()
    unsubscribe = _watch(client, None, queue)
    try:
        started: types.File = convert(await client.invoke(to_builtins(request)), types.File)
        if started.remote.is_uploading_completed:
            await _report(progress, started.remote.uploaded_size, started.size)
            return started
        return await asyncio.wait_for(_await_upload(queue, started, progress), timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise FileTransferError(f"upload did not finish in {timeout}s") from exc
    finally:
        unsubscribe()


async def _await_upload(
    queue: asyncio.Queue[types.File],
    started: types.File,
    progress: ProgressCallback | None,
) -> types.File:
    """Watch ``updateFile`` until the upload finishes or gives up."""
    was_active = started.remote.is_uploading_active
    while True:
        file = await queue.get()
        if file.id != started.id:
            continue  # the queue accepted every file; this one is not ours
        total = file.size or file.expected_size
        await _report(progress, file.remote.uploaded_size, total)

        if file.remote.is_uploading_completed:
            return file
        if was_active and not file.remote.is_uploading_active:
            raise FileTransferError(
                f"upload of file {file.id} stopped at {file.remote.uploaded_size}/{total} bytes"
            )
        was_active = was_active or file.remote.is_uploading_active
