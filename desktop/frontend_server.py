from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.logging_config import get_logger


logger = get_logger("frontend")
PACKAGED_FRONTEND_URL = "http://127.0.0.1:5174/"
_FRONTEND_HOST = "127.0.0.1"
_FRONTEND_PORT = 5174


class _FrontendRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        logger.info(
            "static request client=%s request=%s status=%s bytes=%s",
            self.client_address[0],
            args[2] if len(args) > 2 else "unknown",
            args[3] if len(args) > 3 else "unknown",
            args[4] if len(args) > 4 else "unknown",
        )

    def log_error(self, format: str, *args: object) -> None:
        logger.warning(
            "static request error client=%s detail=%s",
            self.client_address[0],
            format % args,
        )


def start_packaged_frontend(dist_directory: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Serve the packaged Vite build over loopback so root-relative assets resolve."""
    dist_directory = dist_directory.resolve()
    index_path = dist_directory / "index.html"
    if not index_path.is_file():
        raise FileNotFoundError(f"packaged frontend entry not found: {index_path}")

    handler = functools.partial(_FrontendRequestHandler, directory=str(dist_directory))
    try:
        server = ThreadingHTTPServer((_FRONTEND_HOST, _FRONTEND_PORT), handler)
    except OSError:
        logger.exception(
            "failed to bind packaged frontend address=%s:%s; refusing to use another port",
            _FRONTEND_HOST,
            _FRONTEND_PORT,
        )
        raise
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, name="ohmyclash-frontend", daemon=True)
    try:
        thread.start()
    except BaseException:
        server.server_close()
        raise

    logger.info(
        "packaged frontend server ready url=%s directory=%s",
        PACKAGED_FRONTEND_URL,
        dist_directory,
    )
    return server, thread


def stop_packaged_frontend(server: ThreadingHTTPServer | None, thread: threading.Thread | None) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
    if thread is not None:
        thread.join(timeout=5)
        if thread.is_alive():
            logger.warning("packaged frontend server thread did not stop within 5 seconds")
    logger.info("packaged frontend server stopped")
