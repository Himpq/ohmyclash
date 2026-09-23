from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from .app_runtime import ManagedRuntime
from .mihomo_manager import ControllerError, ManagerError, MihomoManager
from .traffic_stats import HourlyTrafficStats
from .windows_proxy import set_system_proxy, system_proxy_status
from .logging_config import get_logger


logger = get_logger("api")


class BackendHandler(BaseHTTPRequestHandler):
    server: "BackendServer"

    def _start_core(self, core: dict[str, Any]) -> None:
        runtime = self.server.runtime
        if runtime is None:
            raise ManagerError("managed runtime is not configured")
        try:
            self.server.manager.add_instance(runtime.build_instance(core["id"]))
        except Exception:
            logger.exception("new core startup failed; rolling back core=%s", core["id"])
            try:
                runtime.delete_core(core["id"])
            except Exception:
                logger.exception("failed to roll back core=%s", core["id"])
            raise
        if self.server.traffic_stats is not None:
            try:
                self.server.traffic_stats.sample_instance(core["id"])
            except Exception:
                logger.exception("initial traffic sample failed after core startup core=%s", core["id"])

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._write_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        self.server.log(format % args)

    def _handle(self, method: str) -> None:
        try:
            path = urlsplit(self.path).path
            query = dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True))
            if path == "/api/health" and method == "GET":
                self._json(200, {"ok": True})
                return
            if path == "/api/instances" and method == "GET":
                self._json(200, {"instances": self.server.manager.statuses()})
                return
            if path == "/api/stats/hourly" and method == "GET":
                if self.server.traffic_stats is None:
                    self._json(503, {"error": "managed runtime is not configured"})
                    return
                self._json(200, self.server.traffic_stats.daily(query.get("date")))
                return

            if path == "/api/profiles" and method == "GET":
                self._json(200, {"profiles": self.server.runtime.profile_infos()})
                return
            if path == "/api/cores" and method == "GET":
                self._json(200, {"cores": self.server.runtime.core_infos()})
                return
            if path == "/api/system-proxy" and method == "GET":
                status = system_proxy_status()
                status["coreId"] = self.server.runtime.core_id_for_proxy_server(status["server"]) if status["enabled"] else None
                self._json(200, status)
                return
            if path == "/api/system-proxy" and method == "PUT":
                with self.server.lifecycle_lock:
                    payload = self._read_json_object()
                    enabled = payload.get("enabled") is True
                    core_id = self._required_string(payload, "coreId")
                    core = self.server.runtime.get_core(core_id)
                    if enabled and not self.server.manager.status(core_id)["running"]:
                        raise ManagerError("cannot enable system proxy for a stopped core")
                    status = set_system_proxy(enabled, core.mixed_port)
                    status["coreId"] = core.id if status["enabled"] else None
                    self._json(200, status)
                return
            if path == "/api/cores" and method == "POST":
                with self.server.lifecycle_lock:
                    payload = self._read_json_object()
                    core = self.server.runtime.create_core(self._required_string(payload, "name"), self._required_string(payload, "profileId"))
                    self._start_core(core)
                    self._json(200, {"core": core, "instances": self.server.manager.statuses()})
                return
            if path == "/api/profiles/import" and method == "POST":
                with self.server.lifecycle_lock:
                    payload = self._read_json_object()
                    profile = self.server.runtime.import_profile(
                        self._required_string(payload, "name"),
                        self._required_string(payload, "content"),
                    )
                    core = self.server.runtime.ensure_default_core(profile["id"])
                    if core is not None:
                        logger.info("first profile imported; starting the default core")
                        self._start_core(core)
                    response: dict[str, Any] = {"profile": profile, "instances": self.server.manager.statuses()}
                    if core is not None:
                        response["core"] = core
                    self._json(200, response)
                return
            if path == "/api/profiles/subscribe" and method == "POST":
                with self.server.lifecycle_lock:
                    payload = self._read_json_object()
                    profile = self.server.runtime.add_subscription(
                        self._required_string(payload, "name"),
                        self._required_string(payload, "url"),
                    )
                    core = self.server.runtime.ensure_default_core(profile["id"])
                    if core is not None:
                        logger.info("first subscription imported; starting the default core")
                        self._start_core(core)
                    response: dict[str, Any] = {"profile": profile, "instances": self.server.manager.statuses()}
                    if core is not None:
                        response["core"] = core
                    self._json(200, response)
                return

            parts = path.split("/")
            if len(parts) == 5 and parts[1:3] == ["api", "profiles"] and parts[4] == "refresh" and method == "POST":
                with self.server.lifecycle_lock:
                    profile = self.server.runtime.refresh_profile(unquote(parts[3]))
                    self._json(200, {"profile": profile, "instances": self.server.reload_runtime()})
                return
            if len(parts) == 5 and parts[1:3] == ["api", "profiles"] and parts[4] == "content" and method == "GET":
                self._json(200, self.server.runtime.profile_content(unquote(parts[3])))
                return
            if len(parts) == 4 and parts[1:3] == ["api", "profiles"] and method == "PUT":
                with self.server.lifecycle_lock:
                    payload = self._read_json_object()
                    profile = self.server.runtime.update_profile(unquote(parts[3]), self._required_string(payload, "name"), self._required_string(payload, "content"))
                    self._json(200, {"profile": profile, "instances": self.server.reload_runtime()})
                return
            if len(parts) == 4 and parts[1:3] == ["api", "profiles"] and method == "DELETE":
                with self.server.lifecycle_lock:
                    profile = self.server.runtime.delete_profile(unquote(parts[3]))
                    self._json(200, {"profile": profile})
                return
            if len(parts) == 5 and parts[1:3] == ["api", "profiles"] and parts[4] == "reveal" and method == "POST":
                self.server.runtime.reveal_profile(unquote(parts[3]))
                self._json(200, {"ok": True})
                return

            if len(parts) == 4 and parts[1:3] == ["api", "cores"] and method == "PUT":
                with self.server.lifecycle_lock:
                    core_id = unquote(parts[3])
                    previous_cores = self.server.runtime.list_cores()
                    previous_manager = self.server.manager
                    proxy_status = system_proxy_status()
                    proxy_core_id = self.server.runtime.core_id_for_proxy_server(proxy_status["server"]) if proxy_status["enabled"] else None
                    catalog_updated = False
                    try:
                        core = self.server.runtime.update_core(core_id, self._read_json_object())
                        catalog_updated = True
                        instances = self.server.reload_runtime()
                        if proxy_status["enabled"] and proxy_core_id == core["id"]:
                            set_system_proxy(True, int(core["mixedPort"]))
                    except Exception:
                        if catalog_updated:
                            logger.exception("core update failed; rolling back core=%s", core_id)
                            try:
                                self.server.runtime.restore_cores(previous_cores)
                            except Exception:
                                logger.exception("failed to restore core catalog after update failure core=%s", core_id)
                            if self.server.manager is not previous_manager or previous_manager.is_closed:
                                try:
                                    self.server.reload_runtime()
                                    if proxy_status["enabled"] and proxy_core_id:
                                        previous_proxy_core = next((item for item in previous_cores if item.id == proxy_core_id), None)
                                        if previous_proxy_core:
                                            set_system_proxy(True, previous_proxy_core.mixed_port)
                                except Exception:
                                    logger.exception("failed to restore runtime after core update failure core=%s", core_id)
                            else:
                                logger.info("core update preflight failed; existing runtime remains active core=%s", core_id)
                        raise
                    self._json(200, {"core": core, "instances": instances})
                return
            if len(parts) == 4 and parts[1:3] == ["api", "cores"] and method == "DELETE":
                with self.server.lifecycle_lock:
                    core_id = unquote(parts[3])
                    if self.server.traffic_stats is not None:
                        self.server.traffic_stats.sample_instance(core_id, final=True)
                    proxy_status = system_proxy_status()
                    if proxy_status["enabled"] and self.server.runtime.core_id_for_proxy_server(proxy_status["server"]) == core_id:
                        core = self.server.runtime.get_core(core_id)
                        set_system_proxy(False, core.mixed_port)
                    self.server.manager.stop(core_id)
                    core = self.server.runtime.delete_core(core_id)
                    self._json(200, {"core": core, "instances": self.server.reload_runtime()})
                return
            if len(parts) == 5 and parts[1:3] == ["api", "cores"] and parts[4] == "mode" and method == "PUT":
                with self.server.lifecycle_lock:
                    core_id = unquote(parts[3])
                    payload = self._read_json_object()
                    previous = self.server.runtime.get_core(core_id)
                    requested_mode = self.server.runtime.normalize_core_mode(payload.get("mode"))
                    running = self.server.manager.status(core_id)["running"]
                    if running:
                        self.server.manager.request(core_id, "PATCH", "/configs", payload={"mode": requested_mode})
                    try:
                        core = self.server.runtime.update_core_mode(core_id, requested_mode)
                    except Exception:
                        if running:
                            try:
                                self.server.manager.request(core_id, "PATCH", "/configs", payload={"mode": previous.mode})
                            except Exception:
                                logger.exception("failed to restore runtime mode core=%s mode=%s", core_id, previous.mode)
                        raise
                    self._json(200, {"core": core})
                return

            if len(parts) < 4 or parts[1:3] != ["api", "instances"]:
                self._json(404, {"error": "route not found"})
                return
            instance_id = unquote(parts[3])
            tail = "/" + "/".join(parts[4:]) if len(parts) > 4 else ""

            if method == "POST" and tail in {"/start", "/stop", "/restart"}:
                with self.server.lifecycle_lock:
                    action = tail[1:]
                    if action in {"stop", "restart"} and self.server.traffic_stats is not None:
                        self.server.traffic_stats.sample_instance(instance_id, final=True)
                    result = getattr(self.server.manager, action)(instance_id)
                    if action in {"start", "restart"} and self.server.traffic_stats is not None:
                        self.server.traffic_stats.sample_instance(instance_id)
                    self._json(200, result)
                return
            if method == "GET" and tail == "/status":
                self._json(200, self.server.manager.status(instance_id))
                return
            if method == "GET" and tail == "/runtime-logs":
                self._json(200, {"logs": self.server.manager.runtime_logs(instance_id)})
                return
            if method == "DELETE" and tail == "/runtime-logs":
                self._json(200, self.server.manager.clear_logs(instance_id))
                return

            payload = self._read_json() if method in {"POST", "PUT", "PATCH"} else None
            status, headers, body = self.server.manager.request(instance_id, method, tail or "/", query, payload)
            self._raw(status, headers, body)
        except ControllerError as exc:
            logger.warning("controller error method=%s path=%s status=%s", method, path, exc.status)
            self._raw(exc.status, exc.headers, exc.body)
        except (ManagerError, ValueError) as exc:
            logger.warning("request rejected method=%s path=%s error=%s", method, path, exc)
            self._json(400, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            logger.exception("backend request failed method=%s path=%s", method, path)
            self._json(500, {"error": "internal backend error"})

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_json_object(self) -> dict[str, Any]:
        payload = self._read_json()
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value

    def _json(self, status: int, payload: Any) -> None:
        self._raw(status, {"Content-Type": "application/json; charset=utf-8"}, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _raw(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        self._write_cors_headers()
        content_type = headers.get("Content-Type", "application/octet-stream")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _write_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        allowed = self.server.allowed_origins
        if "*" in allowed:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")


class BackendServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        manager: MihomoManager,
        allowed_origins: list[str],
        runtime: ManagedRuntime | None = None,
    ):
        super().__init__(address, BackendHandler)
        self.manager = manager
        self.allowed_origins = allowed_origins
        self.runtime = runtime
        self.lifecycle_lock = threading.RLock()
        self._runtime_generation = 0
        self._closing = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self.traffic_stats = HourlyTrafficStats(runtime.data_dir, self) if runtime is not None else None

    def reload_runtime(self) -> list[dict[str, Any]]:
        if self.runtime is None:
            raise ManagerError("managed runtime is not configured")
        if self._closing.is_set():
            raise ManagerError("backend is shutting down")
        with self.lifecycle_lock:
            if self._closing.is_set():
                raise ManagerError("backend is shutting down")
            generation = self._runtime_generation + 1
            logger.info("runtime reload started generation=%s", generation)
            try:
                new_manager = self.runtime.build_manager()
            except Exception:
                logger.exception("runtime reload preflight failed; existing cores remain active generation=%s", generation)
                raise
            if self.traffic_stats is not None:
                self.traffic_stats.sample_all(final=True)
            old_manager = self.manager
            old_manager.close()
            try:
                new_manager.start_autostart()
                new_manager.wait_for_ready()
            except Exception:
                new_manager.close()
                logger.exception("runtime reload failed generation=%s", generation)
                raise
            self.manager = new_manager
            self._runtime_generation = generation
            if self.traffic_stats is not None:
                self.traffic_stats.sample_all()
            logger.info("runtime reload completed generation=%s instances=%s", generation, new_manager.instance_ids)
            return new_manager.statuses()

    def stop_runtime(self) -> None:
        """Stop serving requests and all managed cores exactly once."""
        with self._shutdown_lock:
            if self._shutdown_started:
                logger.info("runtime shutdown already started")
                return
            self._shutdown_started = True
            self._closing.set()

        logger.info("runtime shutdown started")
        try:
            self.shutdown()
        finally:
            try:
                self.server_close()
            finally:
                if self.traffic_stats is not None:
                    self.traffic_stats.close()
                with self.lifecycle_lock:
                    self.manager.close()
                logger.info("runtime shutdown completed")

    @staticmethod
    def log(message: str) -> None:
        logger.debug("%s", message)
