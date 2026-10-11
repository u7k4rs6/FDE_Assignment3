from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
sys.path.insert(0, str(ROOT))

from scripts.mock_api import base_url, is_healthy, start_mock_api, stop, wait_healthy  # noqa: E402

UI_PORT = int(os.getenv("UI_PORT", "8501"))


def _handle_termination(signum: int, frame: object) -> None:
    """Route SIGTERM through normal cleanup (useful for IDE/terminal stop actions)."""
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    procs: list[subprocess.Popen] = []
    url = base_url()  # single source of truth for the API port (was hardcoded 8001)
    try:
        if is_healthy(url):
            print(f"Vendor-risk API already running at {url} - reusing it.")
        else:
            print(f"Starting vendor-risk API on {url} ...")
            api_proc = start_mock_api(url=url, quiet=False)
            procs.append(api_proc)
            wait_healthy(api_proc, url)
            print("Vendor-risk API is ready.")

        try:
            __import__("streamlit")
        except ImportError:
            print("Streamlit is not installed. Run: pip install -r requirements.txt")
            print("The mock API is still running. Press Ctrl+C to stop.")
        else:
            print(f"Starting Procurement Copilot UI on http://127.0.0.1:{UI_PORT} ...")
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(UI_PORT),
                     "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                    cwd=ROOT,
                )
            )

        while True:
            time.sleep(1)
            for proc in procs:
                if proc.poll() is not None:
                    raise RuntimeError(f"A local process exited with code {proc.returncode}")
    except KeyboardInterrupt:
        print("\nStopping local services ...")
    finally:
        for proc in procs:
            stop(proc)


if __name__ == "__main__":
    main()
