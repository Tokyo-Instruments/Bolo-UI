"""Run blocking hardware calls off the Tk thread.

Tk is not thread safe: worker and device threads never touch widgets. They put
callables on ui_queue, and the Tk thread runs them from Worker.poll() (called
every 50 ms via root.after).
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
from typing import Any, Callable

log = logging.getLogger(__name__)


class Worker:
    def __init__(self):
        self.ui_queue: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self.busy = False
        self.current = ""
        self.on_busy_changed: Callable[[bool, str], None] | None = None
        threading.Thread(target=self._run, daemon=True, name="worker").start()

    def submit(self, name: str, fn: Callable, *args,
               on_done: Callable[[Any], None] | None = None,
               on_error: Callable[[BaseException], None] | None = None, **kwargs) -> None:
        """Queue fn(*args, **kwargs); on_done(result) / on_error(exc) run on the Tk thread."""
        self._jobs.put((name, fn, args, kwargs, on_done, on_error))

    def call_in_ui(self, fn: Callable, *args) -> None:
        """Thread-safe: schedule fn(*args) on the Tk thread."""
        self.ui_queue.put((fn, args))

    def poll(self) -> None:
        """Run pending UI callbacks. Call from the Tk thread only."""
        while True:
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception:                            # noqa: BLE001
                log.exception("UI callback failed")

    def _set_busy(self, busy: bool, name: str) -> None:
        self.busy, self.current = busy, name
        if self.on_busy_changed:
            self.call_in_ui(self.on_busy_changed, busy, name)

    def _run(self) -> None:
        while True:
            name, fn, args, kwargs, on_done, on_error = self._jobs.get()
            self.abort.clear()
            self._set_busy(True, name)
            log.info("▶ %s", name)
            try:
                result = fn(*args, **kwargs)
            except BaseException as e:                   # noqa: BLE001 - report everything
                if type(e).__name__ == "Aborted":
                    log.warning("■ %s aborted", name)
                else:
                    log.error("✖ %s failed: %s\n%s", name, e, traceback.format_exc().rstrip())
                if on_error:
                    self.call_in_ui(on_error, e)
            else:
                log.info("✔ %s done", name)
                if on_done:
                    self.call_in_ui(on_done, result)
            finally:
                self._set_busy(not self._jobs.empty(), "")
