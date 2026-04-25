"""File locking with stale detection for ServerPulse Agent."""

import json
import os
import platform
import signal
import time

from models.limits import LOCK_MAX_AGE_SECONDS, LOCK_RETRY_SECONDS, LOCK_RETRY_MAX


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        return False


def _our_pid() -> int:
    try:
        return os.getpid()
    except Exception:
        return 0


class FileLock:
    def __init__(self, lock_path: str, timeout: int = LOCK_MAX_AGE_SECONDS):
        self.lock_path = lock_path
        self.timeout = timeout
        self._acquired = False

    def acquire(self, blocking: bool = True, retry_count: int = LOCK_RETRY_MAX) -> bool:
        start = time.time()
        attempt = 0

        while True:
            if self._try_acquire():
                self._acquired = True
                return True

            if not blocking:
                return False

            if attempt >= retry_count:
                return False

            elapsed = time.time() - start
            if elapsed >= self.timeout:
                return False

            attempt += 1
            sleep_time = min(LOCK_RETRY_SECONDS, self.timeout - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        return False

    def _try_acquire(self) -> bool:
        now = time.time()

        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pid = data.get("pid", 0)
                age = now - data.get("ts", 0)

                if age < self.timeout and pid > 0 and _pid_alive(pid):
                    return False

                os.remove(self.lock_path)
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass

        try:
            lock_dir = os.path.dirname(self.lock_path)
            if lock_dir and not os.path.exists(lock_dir):
                os.makedirs(lock_dir)

            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": _our_pid(), "ts": now}, f)
            return True
        except OSError:
            return False

    def release(self):
        if self._acquired:
            try:
                if os.path.exists(self.lock_path):
                    os.remove(self.lock_path)
            except OSError:
                pass
            self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def atomic_write(path: str, content: str, encoding: str = "utf-8"):
    dir_path = os.path.dirname(path) if path else "."
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=dir_path if dir_path else ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
