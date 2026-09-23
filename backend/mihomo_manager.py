from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .logging_config import get_logger
from .process_job import ProcessJob


logger = get_logger("manager")


class ManagerError(RuntimeError):
    """A local manager configuration or process error."""


class ControllerError(RuntimeError):
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        super().__init__(f"Mihomo controller returned HTTP {status}")


@dataclass(frozen=True)
class InstanceSpec:
    id: str
    name: str
    core_path: Path
    home_dir: Path
    config_path: Path
    controller_url: str
    secret: str = ""
    args: tuple[str, ...] = ("-d", "{home_dir}", "-f", "{config_path}")
    autostart: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "InstanceSpec":
        required = ("id", "name", "core_path", "home_dir", "controller_url")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise ManagerError(f"instance missing required fields: {', '.join(missing)}")

        home_dir = Path(str(raw["home_dir"])).expanduser()
        config_value = raw.get("config_path", "config.yaml")
        config_path = Path(str(config_value)).expanduser()
        if not config_path.is_absolute():
            config_path = home_dir / config_path

        args = raw.get("args")
        if args is None:
            args = ("-d", "{home_dir}", "-f", "{config_path}")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ManagerError(f"instance {raw['id']} args must be a string array")

        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            core_path=Path(str(raw["core_path"])).expanduser(),
            home_dir=home_dir,
            config_path=config_path,
            controller_url=str(raw["controller_url"]).rstrip("/"),
            secret=str(raw.get("secret", "")),
            args=tuple(args),
            autostart=bool(raw.get("autostart", True)),
        )


@dataclass
class ManagedProcess:
    spec: InstanceSpec
    process: subprocess.Popen[str]
    started_at: float
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=300))


class MihomoManager:
    def __init__(self, specs: list[InstanceSpec]):
        self._specs = {spec.id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ManagerError("instance ids must be unique")
        self._validate_specs(specs)
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()
        self._process_job = ProcessJob()
        self._closed = False

    @staticmethod
    def _validate_specs(specs: list[InstanceSpec]) -> None:
        controller_urls = [spec.controller_url for spec in specs]
        if len(set(controller_urls)) != len(controller_urls):
            raise ManagerError("each Mihomo instance must use a unique controller_url")
        home_dirs = [str(spec.home_dir.resolve()) for spec in specs]
        if len(set(home_dirs)) != len(home_dirs):
            raise ManagerError("each Mihomo instance must use a unique home_dir")

    @property
    def instance_ids(self) -> list[str]:
        return list(self._specs)

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def start_autostart(self) -> None:
        for spec in self._specs.values():
            if spec.autostart:
                self.start(spec.id)

    def wait_for_ready(self, timeout: float = 60.0) -> None:
        for instance_id in self.instance_ids:
            self.wait_for_instance(instance_id, timeout)

    def wait_for_instance(self, instance_id: str, timeout: float = 60.0) -> None:
        spec = self._get_spec(instance_id)
        logger.info("waiting for Mihomo controller id=%s timeout=%ss", instance_id, timeout)
        deadline = time.monotonic() + timeout
        last_error = "controller is not ready"
        while time.monotonic() < deadline:
            with self._lock:
                managed = self._processes.get(instance_id)
                if managed is None or managed.process.poll() is not None:
                    detail = managed.logs[-1] if managed and managed.logs else "no core output"
                    raise ManagerError(f"Mihomo process exited before becoming ready: {spec.name}; last output: {detail}")
            try:
                self.request(instance_id, "GET", "/version")
                return
            except ControllerError as exc:
                if exc.status in {401, 403}:
                    raise ManagerError(f"controller authentication failed: {spec.name}") from exc
                last_error = f"controller returned HTTP {exc.status}"
            except ManagerError as exc:
                last_error = str(exc)
            time.sleep(0.15)
        raise ManagerError(f"timed out waiting for Mihomo controller ({spec.name}): {last_error}")

    def add_instance(self, spec: InstanceSpec, timeout: float = 60.0) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise ManagerError("Mihomo manager is closed")
            if spec.id in self._specs:
                raise ManagerError(f"instance already exists: {spec.id}")
            self._validate_specs([*self._specs.values(), spec])
            self._specs[spec.id] = spec
        try:
            self.start(spec.id)
            self.wait_for_instance(spec.id, timeout)
            return self.status(spec.id)
        except Exception as exc:
            with self._lock:
                managed = self._processes.get(spec.id)
                output = list(managed.logs)[-8:] if managed else []
            if output:
                logger.error("new core startup output id=%s lines=%s", spec.id, output)
            try:
                self.stop(spec.id)
            finally:
                with self._lock:
                    self._processes.pop(spec.id, None)
                    self._specs.pop(spec.id, None)
            if output:
                raise ManagerError(f"{exc}; last output: {output[-1]}") from exc
            raise

    def start(self, instance_id: str) -> dict[str, Any]:
        spec = self._get_spec(instance_id)
        with self._lock:
            if self._closed:
                raise ManagerError("Mihomo manager is closed")
            running = self._processes.get(instance_id)
            if running and running.process.poll() is None:
                return self.status(instance_id)

            if not spec.core_path.is_file():
                raise ManagerError(f"core executable does not exist: {spec.core_path}")
            spec.home_dir.mkdir(parents=True, exist_ok=True)
            if not spec.config_path.is_file():
                raise ManagerError(f"config file does not exist: {spec.config_path}")

            command = [str(spec.core_path), *self._expand_args(spec)]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(spec.home_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise ManagerError(f"failed to start {spec.id}: {exc}") from exc

            try:
                self._process_job.assign(process.pid)
            except OSError as exc:
                logger.exception("failed to attach core to process job id=%s pid=%s", spec.id, process.pid)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise ManagerError(f"failed to attach {spec.id} to the managed process job: {exc}") from exc
            logger.info("core attached to parent-exit process job id=%s pid=%s", spec.id, process.pid)

            managed = ManagedProcess(spec=spec, process=process, started_at=time.time())
            self._processes[instance_id] = managed
            logger.info("core started id=%s name=%s pid=%s", spec.id, spec.name, process.pid)
            threading.Thread(target=self._read_output, args=(managed,), daemon=True).start()
            return self.status(instance_id)

    def stop(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            managed = self._processes.get(instance_id)
            if not managed or managed.process.poll() is not None:
                return self.status(instance_id)
            managed.process.terminate()

        try:
            managed.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("core stop timed out; killing id=%s pid=%s", instance_id, managed.process.pid)
            managed.process.kill()
            managed.process.wait(timeout=5)
        logger.info("core stopped id=%s exit_code=%s", instance_id, managed.process.returncode)
        return self.status(instance_id)

    def restart(self, instance_id: str) -> dict[str, Any]:
        self.stop(instance_id)
        return self.start(instance_id)

    def stop_all(self) -> None:
        for instance_id in self.instance_ids:
            try:
                self.stop(instance_id)
            except Exception:  # noqa: BLE001
                logger.exception("core stop failed id=%s", instance_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.stop_all()
        self._process_job.close()
        logger.info("Mihomo process job closed")

    def status(self, instance_id: str, include_logs: bool = False) -> dict[str, Any]:
        spec = self._get_spec(instance_id)
        with self._lock:
            managed = self._processes.get(instance_id)
            running = bool(managed and managed.process.poll() is None)
            result = {
                "id": spec.id,
                "name": spec.name,
                "running": running,
                "pid": managed.process.pid if managed else None,
                "exitCode": None if running or not managed else managed.process.returncode,
                "startedAt": datetime.fromtimestamp(managed.started_at, timezone.utc).isoformat() if managed else None,
                "controllerUrl": spec.controller_url,
            }
            if include_logs:
                result["logs"] = list(managed.logs)[-80:] if managed else []
            return result

    def statuses(self) -> list[dict[str, Any]]:
        return [self.status(instance_id) for instance_id in self.instance_ids]

    def clear_logs(self, instance_id: str) -> dict[str, Any]:
        self._get_spec(instance_id)
        with self._lock:
            managed = self._processes.get(instance_id)
            if managed:
                managed.logs.clear()
        return self.status(instance_id)

    def runtime_logs(self, instance_id: str) -> list[str]:
        self._get_spec(instance_id)
        with self._lock:
            managed = self._processes.get(instance_id)
            return list(managed.logs)[-80:] if managed else []

    def request(
        self,
        instance_id: str,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        payload: Any | None = None,
        timeout: float = 15,
    ) -> tuple[int, dict[str, str], bytes]:
        spec = self._get_spec(instance_id)
        split = urlsplit(spec.controller_url)
        query_string = urlencode(query or {})
        target = urlunsplit((split.scheme, split.netloc, path, query_string, ""))
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if spec.secret:
            headers["Authorization"] = f"Bearer {spec.secret}"

        request = Request(target, method=method.upper(), data=body, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as exc:
            raise ControllerError(exc.code, exc.read(), dict(exc.headers.items())) from exc
        except URLError as exc:
            raise ManagerError(f"controller request failed for {instance_id}: {exc.reason}") from exc

    def _get_spec(self, instance_id: str) -> InstanceSpec:
        try:
            return self._specs[instance_id]
        except KeyError as exc:
            raise ManagerError(f"unknown Mihomo instance: {instance_id}") from exc

    @staticmethod
    def _expand_args(spec: InstanceSpec) -> list[str]:
        values = {
            "home_dir": str(spec.home_dir),
            "config_path": str(spec.config_path),
            "controller_url": spec.controller_url,
        }
        return [argument.format(**values) for argument in spec.args]

    @staticmethod
    def _read_output(managed: ManagedProcess) -> None:
        stream = managed.process.stdout
        if stream is None:
            return
        for line in stream:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            message = line.rstrip()
            managed.logs.append(f"{timestamp} {message}")


def load_manager_config(path: Path) -> tuple[MihomoManager, dict[str, Any]]:
    if not path.is_file():
        raise ManagerError(f"backend config does not exist: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManagerError(f"invalid backend config JSON: {exc}") from exc
    instances = raw.get("instances")
    if not isinstance(instances, list):
        raise ManagerError("backend config must contain an instances array")
    specs = [InstanceSpec.from_dict(item) for item in instances]
    return MihomoManager(specs), raw.get("backend", {})
