"""Router 运行时：后台取批、调用宿主批量分派。"""

from __future__ import annotations

import logging
import threading

from skein.admission import Event
from skein.host import RouterHost
from skein.router.session import RouterSession

log = logging.getLogger("skein.router.runtime")


class RouterRuntime:
    def __init__(
        self,
        host: RouterHost,
        session: RouterSession,
        batch_size: int = 8,
        interval: float = 0.5,
    ) -> None:
        self._host = host
        self._session = session
        self._batch_size = max(1, batch_size)
        self._interval = max(0.05, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def session(self) -> RouterSession:
        return self._session

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="skein-router", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def submit(self, event: Event, thread_id: str) -> None:
        if self._session.submit(event, thread_id):
            log.warning("router queue full, dropped oldest trigger")

    def step(self) -> list[str]:
        batch = self._session.drain(self._batch_size)
        if not batch:
            return []
        triggers = [(item.event, item.thread_id) for item in batch]
        results = self._host.dispatch_batch(triggers, self._session.history())
        actions = [result.action for result in results]
        self._session.remember_batch(batch, actions)
        return actions

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                actions = self.step()
            except Exception:
                log.exception("router batch failed")
                self._stop.wait(min(self._interval * 2, 5.0))
                continue
            if actions:
                log.info("router batch actions=%s", actions)
            else:
                self._stop.wait(self._interval)
