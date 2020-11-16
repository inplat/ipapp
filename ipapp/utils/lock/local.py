import asyncio
from typing import Dict, List, Optional

from .main import LockConfig, LockerInterface


class LocalLock(LockerInterface):
    """
    Управляет блокировками по текстовому ключю в процессе ОС
    Не является потокобезопасным, т.к. использует asyncio.Lock
    """

    def __init__(self, cfg: LockConfig):
        super(LocalLock, self).__init__(cfg)
        self.cfg = cfg
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locking: List[str] = []

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        self._locking.append(key)
        try:
            await asyncio.wait_for(self._locks[key].acquire(), timeout)
        except asyncio.TimeoutError:
            self._locking.remove(key)
            if key not in self._locking:
                self._locks.pop(key)
            raise asyncio.TimeoutError

    async def release(self, key: str) -> None:
        self._locks[key].release()
        self._locking.remove(key)
        if key not in self._locking:
            self._locks.pop(key)
