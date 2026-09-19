"""One-click launcher for the procurement web demo.

Run ``python run.py`` from the project root. The small Tk window chooses the
deterministic model or DeepSeek, then starts the API and static frontend in
child processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BACKEND_PORT = int(os.getenv("PROCUREMENT_BACKEND_PORT", "8000"))
FRONTEND_PORT = int(os.getenv("PROCUREMENT_FRONTEND_PORT", "5173"))
PROCESS_RECORD_PATH = (
    Path(tempfile.gettempdir())
    / "procurement-agent"
    / f"{hashlib.sha256(str(ROOT).casefold().encode('utf-8')).hexdigest()[:16]}-pids.json"
)


class ProcessRecordError(RuntimeError):
    """Raised when the launcher PID record cannot be trusted for cleanup."""


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


def choose_mode() -> str:
    """Return the selected demo or real-model mode."""
    import tkinter as tk

    choice: list[str] = []
    window = tk.Tk()
    window.title("企业采购 Agent")
    window.resizable(False, False)
    tk.Label(window, text="请选择启动模式", padx=44, pady=16, font=("Microsoft YaHei", 12)).pack()
    buttons = tk.Frame(window, padx=16, pady=8)
    buttons.pack()

    def select(mode: str) -> None:
        choice.append(mode)
        window.destroy()

    tk.Button(
        buttons,
        text="Demo 模式(无需LLM)",
        width=25,
        command=lambda: select("demo"),
    ).pack(pady=4)
    tk.Button(
        buttons,
        text="Real 模式",
        width=25,
        command=lambda: select("deepseek"),
    ).pack(pady=4)
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


def listening_pids_by_port(ports: set[int]) -> dict[int, set[int]]:
    """Return Windows listener PIDs grouped by local port."""

    if os.name != "nt" or not ports:
        return {}
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    output = result.stdout.decode(errors="ignore")
    listeners: dict[int, set[int]] = {port: set() for port in ports}
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 5 or columns[0].upper() != "TCP":
            continue
        if columns[-2].upper() not in {"LISTENING", "LISTEN"}:
            continue
        port_match = re.search(r":(\d+)$", columns[1])
        if port_match:
            port = int(port_match.group(1))
            if port in ports:
                listeners[port].add(int(columns[-1]))
    return listeners


def listening_pids(ports: set[int]) -> set[int]:
    """Return all Windows listener PIDs on the requested ports."""

    return {
        pid
        for pids in listening_pids_by_port(ports).values()
        for pid in pids
    }


def process_command_line(pid: int) -> str | None:
    """Read a Windows process command line without terminating the process."""

    if os.name != "nt":
        return None
    command = (
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new(); "
        f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    value = result.stdout.decode("utf-8", errors="ignore").strip()
    return value or None


def read_process_record() -> dict[str, Any] | None:
    """Read the last launcher-owned PID record, if one is available."""

    try:
        payload = json.loads(PROCESS_RECORD_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取本项目进程记录，未清理任何端口进程：{error}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("project_root") != str(ROOT)
        or not isinstance(payload.get("processes"), list)
    ):
        raise ProcessRecordError("本项目进程记录格式无效，未清理任何端口进程。")
    return payload


def wait_for_project_listeners(
    processes: list[subprocess.Popen], roles_by_port: dict[int, str]
) -> dict[int, int]:
    """Wait for each child service and return its actual listening PID."""

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("项目服务启动失败，未写入进程记录。")
        listeners = listening_pids_by_port(set(roles_by_port))
        result: dict[int, int] = {}
        waiting = False
        for port, role in roles_by_port.items():
            pids = listeners.get(port, set())
            if not pids:
                waiting = True
                continue
            if len(pids) != 1:
                raise RuntimeError(f"端口 {port} 被多个进程占用，未写入进程记录。")
            pid = next(iter(pids))
            command_line = process_command_line(pid)
            if command_line is None or not is_project_process({"role": role}, command_line):
                raise RuntimeError(f"端口 {port} 被其他程序占用，未写入进程记录。")
            result[port] = pid
        if not waiting and len(result) == len(roles_by_port):
            return result
        time.sleep(0.2)
    raise RuntimeError("等待项目服务监听端口超时，未写入进程记录。")


def write_process_record(listener_pids: dict[int, int]) -> None:
    """Persist the two child PIDs so a later launcher can identify leftovers."""

    payload = {
        "project_root": str(ROOT),
        "processes": [
            {"role": "backend", "pid": listener_pids[BACKEND_PORT], "port": BACKEND_PORT},
            {"role": "frontend", "pid": listener_pids[FRONTEND_PORT], "port": FRONTEND_PORT},
        ],
    }
    PROCESS_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROCESS_RECORD_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PROCESS_RECORD_PATH)


def clear_process_record() -> None:
    """Remove the launcher-owned PID record after a clean stop or cleanup."""

    try:
        PROCESS_RECORD_PATH.unlink()
    except FileNotFoundError:
        pass


def is_project_process(entry: dict[str, Any], command_line: str) -> bool:
    """Confirm a recorded PID still runs this project's expected server."""

    normalized = command_line.replace("/", "\\").casefold()
    project_root = str(ROOT).replace("/", "\\").casefold()
    role = entry.get("role")
    if project_root not in normalized:
        return False
    if role == "backend":
        return "-m uvicorn" in normalized and "backend.app:app" in normalized
    if role == "frontend":
        frontend_root = str(ROOT / "frontend").replace("/", "\\").casefold()
        return frontend_root in normalized and "static_server.py" in normalized
    return False


def force_kill_process_tree(pid: int) -> None:
    """Terminate a process and every descendant without touching the browser."""

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def cleanup_legacy_processes(ports: set[int]) -> None:
    """Clean only recorded, command-line-verified project server processes."""

    listeners = listening_pids_by_port(ports)
    active_listeners = {
        (port, pid)
        for port, pids in listeners.items()
        for pid in pids
        if pid != os.getpid()
    }
    if not active_listeners:
        clear_process_record()
        return
    record = read_process_record()

    if record is None:
        raise RuntimeError(
            "端口被其他程序占用，且没有可验证的本项目 PID 记录；未清理任何进程。"
        )

    recorded = {}
    for entry in record["processes"]:
        if not isinstance(entry, dict):
            raise ProcessRecordError("本项目进程记录格式无效，未清理任何端口进程。")
        try:
            key = (int(entry["port"]), int(entry["pid"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ProcessRecordError("本项目进程记录格式无效，未清理任何端口进程。") from error
        recorded[key] = entry

    verified_pids: set[int] = set()
    for port, pid in sorted(active_listeners):
        entry = recorded.get((port, pid))
        command_line = process_command_line(pid) if entry else None
        if entry is None or command_line is None or not is_project_process(entry, command_line):
            raise RuntimeError(
                f"端口 {port} 被其他程序占用（PID {pid}），未清理任何进程；"
                "请先释放该端口后再启动。"
            )
        verified_pids.add(pid)

    print(f"清理本项目遗留进程：{sorted(verified_pids)}")
    for pid in sorted(verified_pids):
        force_kill_process_tree(pid)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        remaining = {
            (port, pid)
            for port, pids in listening_pids_by_port(ports).items()
            for pid in pids
            if pid != os.getpid()
        }
        if not remaining:
            clear_process_record()
            return
        time.sleep(0.2)
    raise RuntimeError(f"无法清理本项目遗留进程，端口仍被占用：{sorted(remaining)}")


def stop_processes(processes: list[subprocess.Popen]) -> None:
    """Stop direct children and ensure no descendant process remains."""

    if os.name == "nt":
        # taskkill /T must run while the root is still present; otherwise a
        # detached descendant could outlive its already-exited parent.
        for process in processes:
            if process.poll() is None:
                force_kill_process_tree(process.pid)
    else:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(process.poll() is None for process in processes):
        time.sleep(0.1)

    for process in processes:
        if process.poll() is None:
            if os.name == "nt":
                force_kill_process_tree(process.pid)
            else:
                process.kill()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                force_kill_process_tree(process.pid)
            else:
                process.kill()


def main() -> int:
    mode = choose_mode()
    demo = mode == "demo"
    environment = os.environ.copy()
    if demo:
        environment["PROCUREMENT_DEMO"] = "1"
        environment.pop("PROCUREMENT_MODEL_PROVIDER", None)
    else:
        environment.pop("PROCUREMENT_DEMO", None)
        environment["PROCUREMENT_MODEL_PROVIDER"] = mode
        key_name = {
            "deepseek": "DEEPSEEK_API_KEY",
        }[mode]
        if not environment.get(key_name):
            raise SystemExit(f"{mode.upper()} Real 模式需要先设置 {key_name} 环境变量。")

    try:
        cleanup_legacy_processes({BACKEND_PORT, FRONTEND_PORT})
    except RuntimeError as error:
        print(f"启动终止：{error}", file=sys.stderr)
        return 1
    cache_token = f"{time.time_ns():x}"
    environment["PROCUREMENT_FRONTEND_CACHE_BUST"] = cache_token
    processes: list[subprocess.Popen] = []
    kill_job = WindowsKillJob()
    try:
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app:app",
                "--app-dir",
                str(ROOT),
                "--host",
                "127.0.0.1",
                "--port",
                str(BACKEND_PORT),
            ],
            cwd=ROOT,
            env=environment,
        )
        processes.append(backend)
        kill_job.assign(backend)
        frontend = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "frontend" / "static_server.py"),
                str(FRONTEND_PORT),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(ROOT / "frontend"),
            ],
            cwd=ROOT,
            env=environment,
        )
        processes.append(frontend)
        kill_job.assign(frontend)
        wait_for_backend(backend)
        listener_pids = wait_for_project_listeners(
            processes,
            {BACKEND_PORT: "backend", FRONTEND_PORT: "frontend"},
        )
        write_process_record(listener_pids)
        url = f"http://127.0.0.1:{FRONTEND_PORT}/?v={cache_token}"
        print(f"已启动 {mode} 模式：{url}")
        webbrowser.open(url)
        print("按 Ctrl+C 可停止前后端服务。")
        while True:
            if any(process.poll() is not None for process in processes):
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        # Closing the Job Object is the final, forceful fallback. Windows also
        # closes it automatically if PyCharm kills this launcher outright. Close
        # it before waiting on direct children so all descendants are included.
        kill_job.close()
        stop_processes(processes)
        clear_process_record()


if __name__ == "__main__":
    raise SystemExit(main())
