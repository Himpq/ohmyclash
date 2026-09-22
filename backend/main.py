from __future__ import annotations

import argparse
from pathlib import Path

from .api_server import BackendServer
from .app_runtime import create_managed_runtime
from .mihomo_manager import ManagerError
from .logging_config import configure_logging, get_logger


logger = get_logger("main")


def main() -> int:
    parser = argparse.ArgumentParser(description="OhMyClash Mihomo process manager")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-start", action="store_true", help="start API only; do not launch discovered profiles")
    args = parser.parse_args()
    configure_logging(args.project_root.resolve())

    try:
        runtime = create_managed_runtime(args.project_root.resolve())
        manager = runtime.build_manager()
    except ManagerError as exc:
        logger.exception("runtime initialization failed")
        return 2

    if not args.no_start:
        try:
            manager.start_autostart()
            manager.wait_for_ready()
        except ManagerError as exc:
            manager.close()
            logger.exception("core startup failed")
            return 3

    server = BackendServer(
        ("127.0.0.1", 17890),
        manager,
        ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"],
        runtime=runtime,
    )
    logger.info("backend listening at http://127.0.0.1:17890")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("backend interrupted")
    finally:
        server.stop_runtime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
