"""定时作业驱动：到点开工，按 (job, day) 去重，作业在独立线程里跑。"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from skein import runs

log = logging.getLogger("skein.driver")

Runner = Callable[[str, float], tuple[bool, str]]


@dataclass(frozen=True)
class Schedule:
    job: str
    at: str  # "HH:MM"


def parse_schedules(raw: str) -> list[Schedule]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    result = []
    for item in items:
        job = str(item.get("job") or "").strip()
        at = str(item.get("at") or "").strip()
        if job and len(at) == 5 and at[2] == ":":
            result.append(Schedule(job, at))
    return result


def subprocess_runner(command: str, timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command, check=False, shell=True, capture_output=True, timeout=timeout, text=True
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {int(timeout)}s"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, output[-500:]


class Driver:
    def __init__(
        self,
        conn: sqlite3.Connection,
        schedules: list[Schedule],
        commands: dict[str, str],
        runner: Runner = subprocess_runner,
        timeout: float = 4 * 3600,
        inline: bool = False,
    ) -> None:
        self._conn = conn
        self._schedules = schedules
        self._commands = commands
        self._runner = runner
        self._timeout = timeout
        self._inline = inline
        self._active: set[str] = set()

    def due_jobs(self, now: datetime) -> list[str]:
        stamp = now.strftime("%H:%M")
        return [schedule.job for schedule in self._schedules if schedule.at == stamp]

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        started: list[str] = []
        for job in self.due_jobs(now):
            if job in self._active:
                continue
            run_id = runs.start(self._conn, job, day)
            if run_id is None:
                continue
            command = self._commands.get(job)
            if not command:
                runs.finish(self._conn, run_id, "skipped", "no command configured")
                continue
            started.append(run_id)
            if self._inline:
                self._execute(run_id, job, command)
            else:
                self._active.add(job)
                threading.Thread(
                    target=self._execute, args=(run_id, job, command), daemon=True
                ).start()
        return started

    def _execute(self, run_id: str, job: str, command: str) -> None:
        try:
            ok, detail = self._runner(command, self._timeout)
            runs.finish(self._conn, run_id, "succeeded" if ok else "failed", detail)
            log.info("作业 %s 完成：%s", job, "成功" if ok else "失败")
        except Exception as exc:
            runs.finish(self._conn, run_id, "failed", str(exc))
            log.exception("作业 %s 异常", job)
        finally:
            self._active.discard(job)

    def run_forever(self, interval: float = 30.0, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("调度 tick 失败")
            stop.wait(interval)
