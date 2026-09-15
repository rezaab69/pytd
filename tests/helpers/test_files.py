"""Downloads and uploads: completion signals, progress, and failure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.helpers.files import FileTransferError, download, local_file, upload


def file_payload(
    identifier: int = 5,
    *,
    size: int = 1000,
    downloaded: int = 0,
    completed: bool = False,
    uploaded: int = 0,
    uploading: bool = False,
    upload_done: bool = False,
    path: str = "",
) -> dict[str, Any]:
    return {
        "@type": "file",
        "id": identifier,
        "size": size,
        "expected_size": size,
        "local": {
            "@type": "localFile",
            "path": path,
            "downloaded_size": downloaded,
            "is_downloading_completed": completed,
        },
        "remote": {
            "@type": "remoteFile",
            "uploaded_size": uploaded,
            "is_uploading_active": uploading,
            "is_uploading_completed": upload_done,
        },
    }


# ---- local_file --------------------------------------------------------


def test_local_file_resolves_an_existing_path(tmp_path: Path) -> None:
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"x")

    assert local_file(target).path == str(target.resolve())


def test_local_file_refuses_a_missing_path(tmp_path: Path) -> None:
    # TDLib would report this as a generic 400 from whatever request carried it.
    with pytest.raises(FileNotFoundError, match="no such file"):
        local_file(tmp_path / "absent.jpg")


def test_local_file_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        local_file(tmp_path)


# ---- download ----------------------------------------------------------


async def test_download_returns_the_finished_file(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("downloadFile", file_payload(completed=True, downloaded=1000, path="/tmp/f.jpg"))

    result = await download(client, 5)

    assert result.local.is_downloading_completed
    assert result.local.path == "/tmp/f.jpg"


async def test_download_asks_tdlib_to_wait(client: TypedClient, fake: FakeTDJson) -> None:
    # synchronous=True makes TDLib answer only when the transfer is finished,
    # failed or cancelled, which beats guessing from the update stream.
    fake.on("downloadFile", file_payload(completed=True))

    await download(client, 5, priority=16)
    sent = fake.sent_of("downloadFile")[0]

    assert sent["synchronous"] is True
    assert sent["priority"] == 16
    assert sent["file_id"] == 5


async def test_download_reports_progress(client: TypedClient, fake: FakeTDJson) -> None:
    seen: list[tuple[int, int]] = []

    def respond(_request: dict[str, Any]) -> dict[str, Any] | None:
        for downloaded in (250, 750):
            fake.push(
                {"@type": "updateFile", "file": file_payload(downloaded=downloaded)}, client_id=1
            )
        return file_payload(completed=True, downloaded=1000, path="/tmp/f.jpg")

    fake.on("downloadFile", respond)
    await download(client, 5, progress=lambda done, total: seen.append((done, total)))

    assert seen[-1] == (1000, 1000), "the final call reports completion"
    assert (250, 1000) in seen


async def test_progress_ignores_other_files(client: TypedClient, fake: FakeTDJson) -> None:
    seen: list[tuple[int, int]] = []

    def respond(_request: dict[str, Any]) -> dict[str, Any]:
        fake.push({"@type": "updateFile", "file": file_payload(99, downloaded=500)}, client_id=1)
        return file_payload(completed=True, downloaded=1000)

    fake.on("downloadFile", respond)
    await download(client, 5, progress=lambda done, total: seen.append((done, total)))

    assert (500, 1000) not in seen


async def test_a_raising_progress_callback_does_not_fail_the_download(
    client: TypedClient, fake: FakeTDJson
) -> None:
    # A progress bar is never worth failing a transfer over.
    fake.on("downloadFile", file_payload(completed=True, downloaded=1000))

    def hostile(done: int, total: int) -> None:
        raise ValueError("progress bug")

    result = await download(client, 5, progress=hostile)

    assert result.local.is_downloading_completed


async def test_an_async_progress_callback_is_awaited(client: TypedClient, fake: FakeTDJson) -> None:
    seen: list[int] = []
    fake.on("downloadFile", file_payload(completed=True, downloaded=1000))

    async def record(done: int, total: int) -> None:
        seen.append(done)

    await download(client, 5, progress=record)

    assert seen == [1000]


async def test_a_cancelled_download_is_an_error(client: TypedClient, fake: FakeTDJson) -> None:
    # TDLib answers normally when a download is cancelled; only the flags say so.
    fake.on("downloadFile", file_payload(completed=False, downloaded=400))

    with pytest.raises(FileTransferError, match="400/1000"):
        await download(client, 5)


async def test_download_unsubscribes_afterwards(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("downloadFile", file_payload(completed=True))

    await download(client, 5, progress=lambda done, total: None)

    assert client._observers == []


# ---- upload ------------------------------------------------------------


async def test_upload_waits_for_the_update_stream(
    client: TypedClient, fake: FakeTDJson, tmp_path: Path
) -> None:
    # There is no `synchronous` flag for uploads, so completion has to be read
    # off updateFile.
    target = tmp_path / "clip.ogg"
    target.write_bytes(b"x")

    def respond(_request: dict[str, Any]) -> dict[str, Any]:
        for uploaded, done in ((300, False), (1000, True)):
            fake.push(
                {
                    "@type": "updateFile",
                    "file": file_payload(uploaded=uploaded, uploading=not done, upload_done=done),
                },
                client_id=1,
            )
        return file_payload(uploading=True)

    fake.on("preliminaryUploadFile", respond)
    seen: list[tuple[int, int]] = []

    result = await upload(
        client, target, progress=lambda done, total: seen.append((done, total)), timeout=5
    )

    assert result.remote.is_uploading_completed
    assert seen == [(300, 1000), (1000, 1000)]


async def test_an_already_uploaded_file_returns_at_once(
    client: TypedClient, fake: FakeTDJson, tmp_path: Path
) -> None:
    target = tmp_path / "clip.ogg"
    target.write_bytes(b"x")
    fake.on("preliminaryUploadFile", file_payload(uploaded=1000, upload_done=True))

    result = await upload(client, target, timeout=5)

    assert result.remote.is_uploading_completed


async def test_an_abandoned_upload_is_an_error(
    client: TypedClient, fake: FakeTDJson, tmp_path: Path
) -> None:
    target = tmp_path / "clip.ogg"
    target.write_bytes(b"x")

    def respond(_request: dict[str, Any]) -> dict[str, Any]:
        fake.push(
            {"@type": "updateFile", "file": file_payload(uploaded=400, uploading=False)},
            client_id=1,
        )
        return file_payload(uploading=True)

    fake.on("preliminaryUploadFile", respond)

    with pytest.raises(FileTransferError, match="400/1000"):
        await upload(client, target, timeout=5)


async def test_upload_times_out_rather_than_hanging(
    client: TypedClient, fake: FakeTDJson, tmp_path: Path
) -> None:
    target = tmp_path / "clip.ogg"
    target.write_bytes(b"x")
    fake.on("preliminaryUploadFile", file_payload(uploading=True))

    with pytest.raises(FileTransferError, match="did not finish"):
        await upload(client, target, timeout=0.05)


async def test_upload_accepts_a_prebuilt_input_file(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("preliminaryUploadFile", file_payload(upload_done=True))

    await upload(client, types.InputFileRemote(id="abc"), timeout=5)

    assert fake.sent_of("preliminaryUploadFile")[0]["file"]["@type"] == "inputFileRemote"


async def test_upload_refuses_a_missing_path(client: TypedClient, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await upload(client, tmp_path / "absent.ogg")


async def test_upload_unsubscribes_afterwards(
    client: TypedClient, fake: FakeTDJson, tmp_path: Path
) -> None:
    target = tmp_path / "clip.ogg"
    target.write_bytes(b"x")
    fake.on("preliminaryUploadFile", file_payload(uploading=True))

    with pytest.raises(FileTransferError):
        await upload(client, target, timeout=0.05)

    assert client._observers == []
