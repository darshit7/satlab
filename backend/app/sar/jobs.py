"""The queue in front of the SAR worker process (asyncio side; imports nothing heavy).

One comparison runs at a time, first come first served, behind a small queue cap. Each runs in a
fresh process (spawned, niced, its memory returned to the system when it exits), under a time
limit: past it the process is killed. A crash (an out-of-memory kill, say) fails that comparison
and the next one gets a new process.
"""

import asyncio
import contextlib
import logging
import multiprocessing
import os
import signal
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from app.sar.protocol import Stage, WorkerJob, WorkerResult
from app.sar.worker import init_worker, run_comparison

log = logging.getLogger(__name__)

#: How often a running job's stage is read.
_STAGE_POLL_S = 0.5


class QueueFull(Exception):
    """The queue is at its cap; try again later."""


class JobRunner:
    """FIFO of comparison ids, one consumer, one worker process per job.

    ``process(comparison_id)`` is the service's callback that runs one comparison (building its
    :class:`WorkerJob` and calling :meth:`execute`). ``executor_factory`` replaces the process
    pool in tests (a thread pool runs the same code in-process).
    """

    def __init__(
        self,
        *,
        queue_max: int,
        timeout_s: float,
        nice: int,
        process: Callable[[int], Awaitable[None]],
        executor_factory: Callable[[], Executor] | None = None,
    ) -> None:
        self._queue_max = queue_max
        self._timeout_s = timeout_s
        self._nice = nice
        self._process = process
        self._executor_factory = executor_factory
        self._waiting: deque[tuple[int, int]] = deque()  # (comparison id, session id)
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running: tuple[int, int] | None = None
        self._stage: Stage | None = None
        self._running_pid: int | None = None

    def start(self) -> None:
        """Start the consumer task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="sar-jobs")

    async def stop(self) -> None:
        """Drop the queue and stop the consumer (a running worker is killed)."""
        self._waiting.clear()
        self._kill_running()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def submit(self, comparison_id: int, session_id: int) -> None:
        """Queue a comparison; ``QueueFull`` at the cap."""
        if len(self._waiting) >= self._queue_max:
            raise QueueFull
        self._waiting.append((comparison_id, session_id))
        self._wakeup.set()

    def position(self, comparison_id: int) -> int | None:
        """1 for the next to run, 2 after it…; 0 while running; None if not queued."""
        if self._running is not None and self._running[0] == comparison_id:
            return 0
        for i, (cid, _) in enumerate(self._waiting, start=1):
            if cid == comparison_id:
                return i
        return None

    def stage(self, comparison_id: int) -> Stage | None:
        """The running comparison's stage, else None."""
        if self._running is not None and self._running[0] == comparison_id:
            return self._stage
        return None

    @property
    def busy(self) -> int:
        """Comparisons queued or running."""
        return len(self._waiting) + (1 if self._running is not None else 0)

    def cancel(self, comparison_id: int) -> bool:
        """Drop a queued comparison, or kill it if it is running; whether it was found."""
        for item in list(self._waiting):
            if item[0] == comparison_id:
                self._waiting.remove(item)
                return True
        if self._running is not None and self._running[0] == comparison_id:
            self._kill_running()
            return True
        return False

    def cancel_session(self, session_id: int) -> list[int]:
        """Drop a session's queued comparisons and kill its running one; their ids."""
        dropped = [cid for cid, sid in self._waiting if sid == session_id]
        self._waiting = deque(item for item in self._waiting if item[1] != session_id)
        if self._running is not None and self._running[1] == session_id:
            dropped.append(self._running[0])
            self._kill_running()
        return dropped

    async def _run(self) -> None:
        while True:
            while not self._waiting:
                self._wakeup.clear()
                await self._wakeup.wait()
            self._running = self._waiting.popleft()
            try:
                await self._process(self._running[0])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("SAR comparison %d: processing failed", self._running[0])
            finally:
                self._running = None
                self._stage = None

    async def execute(
        self, job: WorkerJob, on_stage: Callable[[Stage], Awaitable[None]]
    ) -> WorkerResult:
        """Run ``job`` in a new worker process within the time limit."""
        loop = asyncio.get_running_loop()
        control = None
        if self._executor_factory is not None:
            executor = self._executor_factory()
        else:
            ctx = multiprocessing.get_context("spawn")
            control = _Control(ctx)
            executor = ProcessPoolExecutor(
                max_workers=1,
                mp_context=ctx,
                initializer=init_worker,
                initargs=(self._nice, control),
            )
        future = loop.run_in_executor(executor, run_comparison, job)
        deadline = loop.time() + self._timeout_s
        last_stage: Stage | None = None
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    self._kill(control)
                    return WorkerResult(ok=False, error_code="timeout")
                done, _ = await asyncio.wait({future}, timeout=min(_STAGE_POLL_S, remaining))
                if control is not None:
                    self._running_pid = control.pid.value or None
                    stage = Stage(control.stage.value)
                    if stage != last_stage and stage != Stage.IDLE:
                        last_stage = stage
                        self._stage = stage
                        await on_stage(stage)
                if done:
                    try:
                        return future.result()
                    except BrokenProcessPool:
                        return WorkerResult(ok=False, error_code="worker_crashed")
                    except Exception:
                        log.exception("SAR comparison %d: worker error", job.comparison_id)
                        return WorkerResult(ok=False, error_code="worker_crashed")
        finally:
            self._running_pid = None
            executor.shutdown(wait=False, cancel_futures=True)

    def _kill(self, control: "_Control | None") -> None:
        pid = control.pid.value if control is not None else 0
        if pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)

    def _kill_running(self) -> None:
        if self._running_pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(self._running_pid, signal.SIGKILL)


class _Control:
    """Values shared with the worker: its pid and stage."""

    def __init__(self, ctx: Any) -> None:
        self.pid = ctx.Value("i", 0, lock=False)
        self.stage = ctx.Value("i", 0, lock=False)
