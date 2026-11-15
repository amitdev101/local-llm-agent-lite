"""Run the Playground's SearXNG installation on Windows."""

import asyncio
import os
import sys
from pathlib import Path
from threading import Thread


def stop_on_parent_exit():
    # The Playground owns the other end of this pipe.
    sys.stdin.buffer.read(1)
    os._exit(0)


def main():
    source, settings, port = sys.argv[1:]
    os.environ["SEARXNG_SETTINGS_PATH"] = settings
    sys.path.insert(0, str(Path(source)))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    Thread(target=stop_on_parent_exit, daemon=True).start()

    from searx.webapp import app
    from waitress import serve

    serve(app, host="127.0.0.1", port=int(port), threads=4)


if __name__ == "__main__":
    main()
