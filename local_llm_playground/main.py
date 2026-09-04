from __future__ import annotations

import threading
import webbrowser
import os
from pathlib import Path

import psutil
import uvicorn


PORT = 8000
ROOT = Path(__file__).resolve().parent


def clear_stale_server() -> None:
    stale = []

    for connection in psutil.net_connections(kind="inet"):
        if (
            connection.status != psutil.CONN_LISTEN
            or not connection.laddr
            or connection.laddr.port != PORT
            or not connection.pid
            or connection.pid == os.getpid()
        ):
            continue

        try:
            process = psutil.Process(connection.pid)
            command = " ".join(process.cmdline()).lower()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            command = ""

        if "local_llm_playground" not in command:
            raise RuntimeError(
                f"Port {PORT} is used by an unrelated process "
                f"(PID {connection.pid}). Close it manually or change the port."
            )

        if process not in stale:
            stale.append(process)

    for process in stale:
        print(f"Stopping stale playground process {process.pid}...")
        process.terminate()

    _, alive = psutil.wait_procs(stale, timeout=3)

    for process in alive:
        process.kill()

    psutil.wait_procs(alive, timeout=2)


def main() -> None:
    clear_stale_server()
    url = f"http://127.0.0.1:{PORT}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "backend:app",
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
        app_dir=str(ROOT),
    )


if __name__ == "__main__":
    main()
