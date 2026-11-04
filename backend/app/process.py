import ctypes
import os
import queue
import subprocess
import threading
from pathlib import Path


class Cancelled(Exception):
    pass


class WindowsJob:
    """Keep native descendants owned even if their worker is terminated."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Limits(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                        ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t),
                        ("PeakJob", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_native(args: list[str], cwd: Path, env: dict, cancelled, log, stdin=None) -> str:
    if cancelled():
        raise Cancelled("Cancelled by user.")
    owner = WindowsJob()
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    except OSError:
        owner.close()
        raise
    stopped = threading.Event()
    threads = []
    try:
        owner.assign(process)
        messages = queue.Queue(maxsize=256)

        def publish(message):
            while not stopped.is_set():
                try:
                    messages.put(message, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def consume(stream, channel):
            try:
                for line in stream:
                    if stopped.is_set():
                        break
                    publish((channel, line.rstrip()))
            finally:
                stream.close()
                publish((channel, None))

        threads = [threading.Thread(target=consume, args=(stream, channel), daemon=True)
                   for stream, channel in ((process.stdout, "out"), (process.stderr, "err"))]
        for thread in threads:
            thread.start()
        output = []
        size = 0
        finished = 0
        while finished < 2:
            if cancelled():
                raise Cancelled("Cancelled by user.")
            try:
                channel, line = messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                finished += 1
            elif channel == "out":
                size += len(line)
                if size > 4_000_000:
                    raise ValueError("Native command produced unexpectedly large metadata output.")
                output.append(line)
            elif line:
                log(line)
        code = process.wait()
        if code:
            raise RuntimeError(f"{Path(args[0]).name} exited with code {code}. See job logs.")
        return "\n".join(output)
    finally:
        stopped.set()
        owner.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=2)
        if not threads:
            process.stdout.close()
            process.stderr.close()
