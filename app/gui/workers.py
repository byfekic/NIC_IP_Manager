"""Background execution for network work (specification sections 26, 27).

Network operations never run on the GUI thread. A single operation lock
guarantees the operator cannot start two network changes at once.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from app.utils.errors import humanize
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class WorkerSignals(QObject):
    """Signals emitted from a worker. Qt marshals them to the GUI thread."""

    finished = Signal(object)
    failed = Signal(object)
    progress = Signal(str)
    done = Signal()


class Worker(QRunnable):
    """Runs one callable on the thread pool.

    The callable may accept a ``progress`` keyword; if it does, it receives a
    function that emits progress text safely to the GUI thread.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, wants_progress: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        if wants_progress:
            self.kwargs["progress"] = lambda text: self._emit("progress", text)

    def _emit(self, signal_name: str, *args) -> None:
        """Emit a result signal, tolerating a window that has already closed.

        If the application is shutting down, the underlying C++ objects may be
        gone by the time a worker finishes. Emitting then raises RuntimeError,
        which would surface as an unhandled error from QRunnable::run.
        """
        try:
            getattr(self.signals, signal_name).emit(*args)
        except RuntimeError:
            log.debug("Dropped '%s' signal: the receiver is gone", signal_name)

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # a worker must never take down the process
            log.exception("Background task failed: %s", self.fn)
            self._emit("failed", humanize(exc))
        else:
            self._emit("finished", result)
        finally:
            self._emit("done")


class OperationLock:
    """Guards against concurrent network operations (section 26)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: Optional[str] = None

    def acquire(self, owner: str) -> bool:
        if self._lock.acquire(blocking=False):
            self._owner = owner
            log.debug("Operation lock acquired by %s", owner)
            return True
        log.warning("Operation lock busy (held by %s); %s refused", self._owner, owner)
        return False

    def release(self) -> None:
        if self._owner is not None:
            log.debug("Operation lock released by %s", self._owner)
            self._owner = None
            try:
                self._lock.release()
            except RuntimeError:  # pragma: no cover - already released
                pass

    @property
    def busy(self) -> bool:
        return self._owner is not None

    @property
    def owner(self) -> Optional[str]:
        return self._owner


class TaskRunner:
    """Convenience wrapper around a QThreadPool."""

    def __init__(self, max_threads: int = 4) -> None:
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Any], None]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> Worker:
        worker = Worker(fn, *args, wants_progress=on_progress is not None, **kwargs)
        if on_result:
            worker.signals.finished.connect(on_result)
        if on_error:
            worker.signals.failed.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_done:
            worker.signals.done.connect(on_done)
        self.pool.start(worker)
        return worker

    def wait(self, timeout_ms: int = 5000) -> bool:
        return self.pool.waitForDone(timeout_ms)
