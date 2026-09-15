"""The 14 parameters TDLib demands before it will do anything else."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, replace
from pathlib import Path

from tdpy import __version__
from tdpy.api import functions
from tdpy.exceptions import TdpyError

__all__ = ["MissingCredentialsError", "TdlibSettings"]

#: Where a session's database lands when the caller does not say.
DEFAULT_DATABASE_DIRECTORY = Path("td_session")


class MissingCredentialsError(TdpyError):
    """An api_id/api_hash pair could not be found."""


@dataclass(frozen=True, slots=True)
class TdlibSettings:
    """Everything ``setTdlibParameters`` needs.

    Get ``api_id`` and ``api_hash`` from https://my.telegram.org/apps. They
    identify the *application*, not the account, and TDLib refuses to start
    without them.

    Args:
        api_id: Application identifier from my.telegram.org.
        api_hash: Application hash from my.telegram.org.
        database_directory: Where TDLib keeps its database. Deleting it logs
            the session out.
        files_directory: Where downloads land. Defaults to the database
            directory, which is also TDLib's own default.
        database_encryption_key: Encrypts the local database. An empty key
            means no encryption; a wrong one fails authorization with 401.
        use_test_dc: Use Telegram's test servers instead of production.
        use_file_database: Remember downloaded and uploaded files across runs.
        use_chat_info_database: Cache users and chats. Implies
            ``use_file_database``.
        use_message_database: Cache chats and messages. Implies
            ``use_chat_info_database``.
        use_secret_chats: Enable secret chat support.
        system_language_code: IETF tag for the OS language.
        device_model: Shown to the user in their active-sessions list.
        system_version: Defaults to this machine's OS version.
        application_version: Shown alongside ``device_model``.
    """

    api_id: int
    api_hash: str
    database_directory: Path = DEFAULT_DATABASE_DIRECTORY
    files_directory: Path | None = None
    database_encryption_key: bytes = b""
    use_test_dc: bool = False
    use_file_database: bool = True
    use_chat_info_database: bool = True
    use_message_database: bool = True
    use_secret_chats: bool = False
    system_language_code: str = "en"
    device_model: str = "tdpy"
    system_version: str = ""
    application_version: str = __version__

    def normalized(self) -> TdlibSettings:
        """Apply TDLib's implied flags so the settings match what it will do.

        ``use_message_database`` implies ``use_chat_info_database`` implies
        ``use_file_database``. TDLib turns them on regardless, so making that
        explicit here keeps the object from lying about the session.
        """
        chat_info = self.use_chat_info_database or self.use_message_database
        return replace(
            self,
            use_chat_info_database=chat_info,
            use_file_database=self.use_file_database or chat_info,
            system_version=self.system_version or f"{platform.system()} {platform.release()}",
        )

    def to_request(self) -> functions.SetTdlibParameters:
        """Render these settings as the TDLib request."""
        settings = self.normalized()
        files = settings.files_directory or settings.database_directory
        return functions.SetTdlibParameters(
            use_test_dc=settings.use_test_dc,
            database_directory=str(settings.database_directory),
            files_directory=str(files),
            database_encryption_key=settings.database_encryption_key,
            use_file_database=settings.use_file_database,
            use_chat_info_database=settings.use_chat_info_database,
            use_message_database=settings.use_message_database,
            use_secret_chats=settings.use_secret_chats,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            system_language_code=settings.system_language_code,
            device_model=settings.device_model,
            system_version=settings.system_version,
            application_version=settings.application_version,
        )

    @classmethod
    def from_env(cls, **overrides: object) -> TdlibSettings:
        """Read ``TDPY_API_ID`` and ``TDPY_API_HASH`` from the environment.

        ``TDPY_DATABASE_DIRECTORY`` is honoured too. Keeping credentials out of
        source is the point, so this raises rather than inventing a default.

        Raises:
            MissingCredentialsError: if either variable is absent or unusable.
        """
        api_id, api_hash = os.environ.get("TDPY_API_ID"), os.environ.get("TDPY_API_HASH")
        if not api_id or not api_hash:
            missing = [
                name
                for name, value in (("TDPY_API_ID", api_id), ("TDPY_API_HASH", api_hash))
                if not value
            ]
            raise MissingCredentialsError(
                f"{' and '.join(missing)} not set; get a pair from https://my.telegram.org/apps"
            )
        try:
            numeric_id = int(api_id)
        except ValueError as exc:
            message = f"TDPY_API_ID must be an integer, got {api_id!r}"
            raise MissingCredentialsError(message) from exc

        directory = os.environ.get("TDPY_DATABASE_DIRECTORY")
        if directory:
            overrides.setdefault("database_directory", Path(directory))
        return cls(api_id=numeric_id, api_hash=api_hash, **overrides)  # type: ignore[arg-type]
