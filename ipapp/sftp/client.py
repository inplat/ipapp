import asyncio
import logging
import os
from asyncio import Future
from enum import Enum
from pathlib import PurePath
from typing import Any, Callable, List, Optional, Union

import asyncssh
from asyncssh import (
    SFTPAttrs,
    SFTPClient,
    SFTPName,
    SSHClient,
    SSHClientConnection,
)
from asyncssh.sftp import _MAX_SFTP_REQUESTS, SFTP_BLOCK_SIZE
from pydantic import AnyUrl, BaseModel, Field

from ..component import Component
from ..error import PrepareError
from ..logger import Span, wrap2span
from ..misc import json_encode as default_json_encode
from ..misc import mask_url_pwd

Path = Union[str, bytes, PurePath]
Paths = Union[str, bytes, PurePath, List[Path]]
ErrorHandler = Callable[[Exception], None]
ProgressHandler = Callable[[str, str, int, int], None]


class LogLevel(str, Enum):
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    WARN = "WARN"
    INFO = "INFO"
    DEBUG = "DEBUG"
    NOTSET = "NOTSET"


class SftpClientSpan(Span):
    KIND_CLIENT = "CLIENT"

    NAME_GET = "sftp::get"
    NAME_PUT = "sftp::put"
    NAME_REMOVE = "sftp::remove"
    NAME_READDIR = "sftp::readdir"
    NAME_LISTDIR = "sftp::listdir"
    NAME_RENAME = "sftp::rename"
    NAME_MKDIR = "sftp::mkdir"
    NAME_CHDIR = "sftp::chdir"
    NAME_GETCWD = "sftp::getcwd"
    NAME_EXISTS = "sftp::exists"

    ANN_EVENT = "event"


class SftpDsn(AnyUrl):
    allowed_schemes = {"sftp"}
    user_required = True


class SftpClientConfig(BaseModel):
    url: SftpDsn = Field(
        SftpDsn(  # nosec
            url="sftp://user:password@localhost:2222/upload",
            scheme="sftp",
            user="user",
            password="password",
            host="localhost",
            port="2222",
            path="/upload",
        ),
        description="URL для подключения к SFTP серверу",
        example="sftp://user:password@localhost:2222/upload",
    )
    key: Optional[str] = Field(
        None,
        description="Путь к приватному ключу в локальной файловой системе",
        example="/root/.ssh/id_rsa",
    )
    passphrase: Optional[str] = Field(
        None, description="Пароль для расшифровки приватных ключей",
    )
    known_hosts: Optional[str] = Field(
        None,
        description=(
            "Путь к файлу known_hosts в локальной файловой системе. "
            "Если не указан, то хост проверяться не будет"
        ),
        example="/root/.ssh/known_hosts",
    )
    connect_max_attempts: Optional[int] = Field(
        None,
        description=(
            "Максимальное количество попыток подключения к SFTP серверу "
            "(если не установлено, то пытается подключиться бесконечно)"
        ),
    )
    connect_retry_delay: float = Field(
        1.0,
        description=(
            "Задержка перед повторной попыткой подключения к SFTP серверу"
        ),
    )
    connect_timeout: float = Field(
        60.0, description="Таймаут подключения к SFTP серверу"
    )
    asyncssh_log_level: LogLevel = Field(
        LogLevel.CRITICAL, description="Уровень логирования asyncssh"
    )


class SftpClient(Component):
    def __init__(
        self,
        cfg: SftpClientConfig,
        *,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        self.cfg = cfg
        self._json_encode = json_encode
        self._client: Optional[SFTPClient] = None
        self._fut: Optional[Future[Any]] = None
        self._conn: Optional[SSHClientConnection] = None
        self._connected = False
        self._stopping = False

        logger = logging.getLogger("asyncssh")
        logger.setLevel(self.cfg.asyncssh_log_level.value)

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = value

    @property
    def stopping(self) -> bool:
        return self._stopping

    @stopping.setter
    def stopping(self, value: bool) -> None:
        self._stopping = value

    @property
    def masked_url(self) -> Optional[str]:
        return mask_url_pwd(str(self.cfg.url))

    async def _connect(self) -> None:
        self._conn, client = await asyncssh.create_connection(
            SshClient,
            host=self.cfg.url.host or "localhost",
            port=int(self.cfg.url.port or 22),
            username=self.cfg.url.user,
            password=self.cfg.url.password,
            known_hosts=self.cfg.known_hosts,
            client_keys=[self.cfg.key] if self.cfg.key else None,
            passphrase=self.cfg.passphrase,
        )

        client._client = self
        self._client = await self._conn.start_sftp_client()

    async def connect(self) -> None:
        if self.app is None:
            raise UserWarning("Unattached component")

        attempts = 0
        while (
            self.cfg.connect_max_attempts is None
            or attempts < self.cfg.connect_max_attempts
        ):
            attempts += 1
            counter = ""

            if attempts > 1:
                counter = (
                    f"{attempts if attempts else ''}/"
                    f"{self.cfg.connect_max_attempts or '~'} "
                    f"in {self.cfg.connect_retry_delay}s "
                )

            try:
                self.app.log_info(
                    "Connecting %sto %s", counter, self.masked_url,
                )
                await asyncio.wait_for(
                    self._connect(), timeout=self.cfg.connect_timeout
                )
            except asyncio.TimeoutError as exc:
                self.app.log_err(
                    "Timeout connection to %s: %s", self.masked_url, exc
                )
                await asyncio.sleep(self.cfg.connect_retry_delay)
            except Exception as exc:
                self.app.log_err(
                    "Could not connect to %s: %s", self.masked_url, exc
                )
                await asyncio.sleep(self.cfg.connect_retry_delay)
            else:
                self.connected = True
                self.app.log_info("Connected to %s", self.masked_url)
                if self.cfg.url.path:
                    if not await self.exists(self.cfg.url.path):
                        raise PrepareError(
                            f"Sftp path '{self.cfg.url.path}' does not exists"
                        )
                    await self.chdir(self.cfg.url.path)
                return

        if (
            self.cfg.connect_max_attempts is not None
            and attempts >= self.cfg.connect_max_attempts
        ):
            raise PrepareError(f"Could not connect to '{self.masked_url}'")

    async def prepare(self) -> None:
        if self.cfg.key is not None and not os.path.exists(self.cfg.key):
            raise PrepareError(
                f"Sftp private key '{self.cfg.key}' does not exists"
            )

        await self.connect()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.stopping = True

        if self._fut is not None and not self._fut.done():
            self._fut.cancel()

        if self._client is not None:
            self._client.exit()
            await self._client.wait_closed()

        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()

    async def health(self) -> None:
        if self._client is not None:
            await self._client.getcwd()

    async def get(
        self,
        remotepaths: Paths,
        localpath: Optional[Path] = None,
        *,
        preserve: bool = False,
        recurse: bool = False,
        follow_symlinks: bool = False,
        block_size: int = SFTP_BLOCK_SIZE,
        max_requests: int = _MAX_SFTP_REQUESTS,
        progress_handler: Optional[ProgressHandler] = None,
        error_handler: Optional[ErrorHandler] = None,
    ) -> None:
        """
        Метод получает файл на удалённом сервере
        и кладет его в локальное хранилище по указанным параметрам
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        event = f"'{localpath!s}' from '{remotepaths!s}'"
        self.app.log_debug("Sftp get %s", event)

        with wrap2span(
            name=SftpClientSpan.NAME_GET,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, event)
            return await self._client.get(
                remotepaths,
                localpath,
                preserve=preserve,
                recurse=recurse,
                follow_symlinks=follow_symlinks,
                block_size=block_size,
                max_requests=max_requests,
                progress_handler=progress_handler,
                error_handler=error_handler,
            )

    async def put(
        self,
        localpaths: Paths,
        remotepath: Optional[Path] = None,
        *,
        preserve: bool = False,
        recurse: bool = False,
        follow_symlinks: bool = False,
        block_size: int = 16384,
        max_requests: int = 128,
        progress_handler: Optional[ProgressHandler] = None,
        error_handler: Optional[ErrorHandler] = None,
    ) -> None:
        """
        Метод кладет локальный файл на удалённый сервер
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        event = f"'{localpaths!s}' to '{remotepath!s}'"
        self.app.log_debug("Sftp put %s", event)

        with wrap2span(
            name=SftpClientSpan.NAME_PUT,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, event)
            await self._client.put(
                localpaths,
                remotepath,
                preserve=preserve,
                recurse=recurse,
                follow_symlinks=follow_symlinks,
                block_size=block_size,
                max_requests=max_requests,
                progress_handler=progress_handler,
                error_handler=error_handler,
            )

    async def remove(self, path: Path) -> None:
        """
        Метод удаляет файл или ссылку на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp remove '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_REMOVE,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            return await self._client.remove(path=path)

    async def readdir(
        self, path: Path = ".", *, log_result: bool = True
    ) -> List[SFTPName]:
        """
        Метод получает список файлов на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp readdir '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_READDIR,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            paths = await self._client.readdir(path=path)
            if log_result:
                span.annotate(
                    SftpClientSpan.ANN_EVENT,
                    self._json_encode([p.filename for p in paths]),
                )
            return paths

    async def listdir(
        self, path: Path = ".", *, log_result: bool = True
    ) -> List[str]:
        """
        Метод получает список имен файлов на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp listdir '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_LISTDIR,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            paths = await self._client.listdir(path=path)
            if log_result:
                span.annotate(
                    SftpClientSpan.ANN_EVENT, self._json_encode(paths)
                )
            return paths

    async def rename(self, oldpath: Path, newpath: Path) -> None:
        """
        Метод переименует файл, директорию или ссылку на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        event = f"'{oldpath!s}' to '{newpath!s}'"
        self.app.log_debug("Sftp rename %s", event)

        with wrap2span(
            name=SftpClientSpan.NAME_RENAME,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, event)
            return await self._client.rename(oldpath=oldpath, newpath=newpath)

    async def mkdir(self, path: Path, attrs: SFTPAttrs = SFTPAttrs()) -> None:
        """
        Метод создает директорию на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp mkdir '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_MKDIR,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            return await self._client.mkdir(path=path, attrs=attrs)

    async def chdir(self, path: Path) -> None:
        """
        Метод меняет текущую рабочую директорию на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp chdir '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_CHDIR,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            return await self._client.chdir(path=path)

    async def getcwd(self) -> str:
        """
        Метод возвращает текущую рабочую директорию на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp getcwd")

        with wrap2span(
            name=SftpClientSpan.NAME_GETCWD,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            path = await self._client.getcwd()
            span.annotate(SftpClientSpan.ANN_EVENT, path)
            return path

    async def exists(self, path: Path) -> bool:
        """
        Метод возвращает true, если путь существует на сервере
        """
        if self.app is None:
            raise UserWarning("Unattached component")

        if self._client is None:
            raise UserWarning("Uninitialized client")

        self.app.log_debug("Sftp exists '%s'", path)

        with wrap2span(
            name=SftpClientSpan.NAME_EXISTS,
            kind=SftpClientSpan.KIND_CLIENT,
            ignore_ctx=True,
            cls=SftpClientSpan,
            app=self.app,
        ) as span:
            span.annotate(SftpClientSpan.ANN_EVENT, str(path))
            exists = await self._client.exists(path=path)
            span.annotate(SftpClientSpan.ANN_EVENT, str(exists))
            return exists


class SshClient(SSHClient):
    def __init__(self) -> None:
        super().__init__()
        self._client: Optional[SftpClient] = None

    def connection_lost(self, exc: Exception) -> None:
        if self._client is None or self._client.stopping:
            return

        self.connected = False

        if self._client._fut is not None and not self._client._fut.done():
            self._client._fut.cancel()

        self._client._fut = asyncio.ensure_future(self._client.connect())
