import asyncio
import os
import signal
import subprocess  # nosec
import sys
from typing import List, Optional, Union

try:
    from watchdog.events import FileSystemEvent
    from watchdog import observers
except ImportError:
    observers = None


_has_execv: bool = sys.platform != 'win32'
_reload_attempted: bool = False


class EventHandler(object):
    def __init__(
        self, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        self._loop: asyncio.AbstractEventLoop = (
            loop or asyncio.get_event_loop()
        )

    async def on_any_event(self, event: 'FileSystemEvent') -> None:
        if event.src_path.lower().endswith('.py'):
            _reload()

    def dispatch(self, event: 'FileSystemEvent') -> None:
        self._loop.call_soon_threadsafe(
            asyncio.create_task, self.on_any_event(event)
        )


class Watchdog(object):
    def __init__(self, path: str = '.') -> None:
        self._observer = observers.Observer()
        self._observer.schedule(EventHandler(), path, True)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()


def _reload() -> None:
    global _reload_attempted
    _reload_attempted = True
    if hasattr(signal, "setitimer"):
        # Clear the alarm signal set by
        # ioloop.set_blocking_log_threshold so it doesn't fire
        # after the exec.
        signal.setitimer(signal.ITIMER_REAL, 0, 0)
    # sys.path fixes: see comments at top of file.  If sys.path[0] is an empty
    # string, we were (probably) invoked with -m and the effective path
    # is about to change on re-exec.  Add the current directory to $PYTHONPATH
    # to ensure that the new process sees the same path we did.
    path_prefix = '.' + os.pathsep
    if sys.path[0] == '' and not os.environ.get("PYTHONPATH", "").startswith(
        path_prefix
    ):
        os.environ["PYTHONPATH"] = path_prefix + os.environ.get(
            "PYTHONPATH", ""
        )

    if not _has_execv:
        subprocess.Popen([sys.executable] + sys.argv)  # nosec
        sys.exit(0)
    else:
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)  # nosec
        except OSError:
            # Mac OS X versions prior to 10.6 do not support execv in
            # a process that contains multiple threads.  Instead of
            # re-executing in the current process, start a new one
            # and cause the current process to exit.  This isn't
            # ideal since the new process is detached from the parent
            # terminal and thus cannot easily be killed with ctrl-C,
            # but it's better than not being able to autoreload at
            # all.
            # Unfortunately the errno returned in this case does not
            # appear to be consistent, so we can't easily check for
            # this error specifically.
            args: List[Union[bytes, str]] = ([sys.executable] + sys.argv)  # type: ignore
            os.spawnv(  # nosec  # type: ignore
                os.P_NOWAIT, sys.executable, args,
            )
            # At this point the IOLoop has been closed and finally
            # blocks will experience errors if we allow the stack to
            # unwind, so just exit uncleanly.
            os._exit(0)


def start(path: Optional[str] = None) -> Watchdog:
    if observers is None:
        raise Exception('watchdog are not installed')
    cwd = os.getcwd()
    sys.path.append(cwd)
    if path is None:
        path = cwd
    wd = Watchdog(path=path)
    wd.start()
    return wd
