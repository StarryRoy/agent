"""One-click launcher for the procurement web demo.

Run ``python run.py`` from the project root.  The small Tk window chooses
between the existing deterministic model and the application-owned Gemini
model, then starts the API and static frontend in child processes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BACKEND_PORT = int(os.getenv("PROCUREMENT_BACKEND_PORT", "8000"))
FRONTEND_PORT = int(os.getenv("PROCUREMENT_FRONTEND_PORT", "5173"))


class WindowsKillJob:
    """Kill every assigned process when this launcher exits for any reason."""

    def __init__(self) -> None:
        self._kernel32: Any = None
        self._handle: int | None = None
        if os.name != "nt":
            return

        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel32.CloseHandle(handle)
            raise error

        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, process: subprocess.Popen) -> None:
        if self._handle is None:
            return
        import ctypes

        if not self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def choose_mode() -> bool:
    """Return True for demo mode, using a minimal native selection window."""
    import tkinter as tk

    choice: list[bool] = []
    window = tk.Tk()
    window.title("企业采购 Agent")
    window.resizable(False, False)
    tk.Label(window, text="请选择启动模式", padx=44, pady=16, font=("Microsoft YaHei", 12)).pack()
    buttons = tk.Frame(window, padx=16, pady=8)
    buttons.pack()

    def select(demo: bool) -> None:
        choice.append(demo)
        window.destroy()

    tk.Button(buttons, text="Demo 模式（无需 API Key）", width=25, command=lambda: select(True)).pack(
        pady=4
    )
    tk.Button(buttons, text="Real 模式（Gemini）", width=25, command=lambda: select(False)).pack(
        pady=4
    )
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    window.mainloop()
    if not choice:
        raise SystemExit("未选择启动模式。")
    return choice[0]


def wait_for_backend(process: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{BACKEND_PORT}/api/v1/health"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("FastAPI 后端启动失败，请检查终端日志。")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError("等待 FastAPI 后端就绪超时。")


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    demo = choose_mode()
    environment = os.environ.copy()
    if demo:
        environment["PROCUREMENT_DEMO"] = "1"
    else:
        environment.pop("PROCUREMENT_DEMO", None)
        if not environment.get("GEMINI_API_KEY"):
            raise SystemExit("Real 模式需要先设置 GEMINI_API_KEY 环境变量。")

    processes: list[subprocess.Popen] = []
    kill_job = WindowsKillJob()
    try:
        backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
            cwd=ROOT,
            env=environment,
        )
        processes.append(backend)
        kill_job.assign(backend)
        frontend = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(FRONTEND_PORT), "--bind", "127.0.0.1", "--directory", "frontend"],
            cwd=ROOT,
            env=environment,
        )
        processes.append(frontend)
        kill_job.assign(frontend)
        wait_for_backend(backend)
        url = f"http://127.0.0.1:{FRONTEND_PORT}/"
        print(f"已启动 {'Demo' if demo else 'Real'} 模式：{url}")
        webbrowser.open(url)
        print("按 Ctrl+C 可停止前后端服务。")
        while True:
            if any(process.poll() is not None for process in processes):
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_processes(processes)
        # Closing the Job Object is the final, forceful fallback. Windows also
        # closes it automatically if PyCharm kills this launcher outright.
        kill_job.close()


if __name__ == "__main__":
    raise SystemExit(main())
