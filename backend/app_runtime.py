from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import hashlib
import ctypes
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

from .mihomo_manager import InstanceSpec, ManagerError, MihomoManager


BUNDLED_GEOIP_METADB_SHA256 = "6eef2fedc2dae7091c112be13895968135ec01e702df2f5b4b1161b07d714720"


@dataclass(frozen=True)
class ManagedProfile:
    id: str
    name: str
    source_path: Path
    source_url: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ManagedCore:
    id: str
    name: str
    profile_id: str
    controller_port: int
    mixed_port: int
    secret: str
    mode: str = "rule"
    tun_enabled: bool = False
    allow_lan: bool = False
    ipv6: bool = False
    log_level: str = "info"


class CoreLocator:
    """Find the bundled core first, then known local development locations."""

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def locate(self) -> Path:
        candidates = [
            self.project_root / "runtime" / "core" / "mihomo.exe",
            self.project_root / "runtime" / "core" / "mihomo-windows-amd64.exe",
            self.project_root / "resources" / "mihomo.exe",
            self.project_root / "resources" / "core" / "mihomo.exe",
        ]
        if os.name == "nt":
            for base in (Path(os.environ.get("ProgramFiles", "C:/Program Files")), Path("F:/Programs")):
                candidates.extend([
                    base / "Mihomo" / "mihomo.exe",
                    base / "Clash" / "resources" / "static" / "files" / "win" / "x64" / "clash-win64.exe",
                ])
        for command in ("mihomo", "clash-meta", "clash"):
            resolved = shutil.which(command)
            if resolved:
                candidates.append(Path(resolved))
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise ManagerError(
            "Mihomo core was not found. Put the bundled binary at runtime/core/mihomo.exe "
            "or install a supported Mihomo binary for automatic discovery."
        )


class ManagedRuntime:
    """Build one isolated Mihomo process from each user-managed YAML profile."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.data_dir = project_root / "data"
        self.profile_dir = self.data_dir / "profiles"
        self.instance_dir = self.data_dir / "runtime" / "instances"
        self.catalog_path = self.data_dir / "profiles.json"
        self.cores_path = self.data_dir / "cores.json"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.instance_dir.mkdir(parents=True, exist_ok=True)

    def build_manager(self) -> MihomoManager:
        cores = self.list_cores(migrate=True)
        if not cores:
            return MihomoManager([])
        core_path = CoreLocator(self.project_root).locate()
        profiles = {profile.id: profile for profile in self.list_profiles()}
        specs = [self._prepare_instance(core, profiles[core.profile_id], core_path) for core in cores if core.profile_id in profiles]
        return MihomoManager(specs)

    def build_instance(self, core_id: str) -> InstanceSpec:
        core = self.get_core(core_id)
        profile = self._find_profile(core.profile_id)
        core_path = CoreLocator(self.project_root).locate()
        return self._prepare_instance(core, profile, core_path)

    def list_cores(self, migrate: bool = False) -> list[ManagedCore]:
        if not self.cores_path.is_file():
            if migrate:
                profiles = self.list_profiles()
                if profiles:
                    self.create_core("默认核心", profiles[0].id)
            if not self.cores_path.is_file():
                return []
        try:
            raw = json.loads(self.cores_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManagerError(f"cannot read core catalog: {exc}") from exc
        if not isinstance(raw, list):
            raise ManagerError("core catalog must be an array")
        cores: list[ManagedCore] = []
        catalog_changed = False
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "mode" not in item:
                item = {**item, "mode": self._profile_mode(str(item.get("profileId", "")))}
                catalog_changed = True
            cores.append(self._core_from_dict(item))
        if catalog_changed:
            self._save_cores(cores)
        return cores

    def core_infos(self) -> list[dict[str, Any]]:
        return [self._core_info(core) for core in self.list_cores(migrate=True)]

    def get_core(self, core_id: str) -> ManagedCore:
        core = next((item for item in self.list_cores(migrate=True) if item.id == core_id), None)
        if core is None:
            raise ManagerError(f"core not found: {core_id}")
        return core

    def core_id_for_proxy_server(self, server: str) -> str | None:
        normalized = server.strip().lower()
        for core in self.list_cores(migrate=True):
            expected = f"127.0.0.1:{core.mixed_port}"
            if normalized == expected or expected in {part.strip() for part in normalized.split(";")}:
                return core.id
        return None

    def create_core(self, name: str, profile_id: str) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ManagerError("core name is required")
        self._find_profile(profile_id)
        cores = self.list_cores() if self.cores_path.is_file() else []
        core_id = self._unique_core_id(clean_name, cores)
        reserved = {port for core in cores for port in (core.controller_port, core.mixed_port)}
        controller_port = self._free_port(reserved)
        reserved.add(controller_port)
        mixed_port = self._free_port(reserved)
        core = ManagedCore(
            core_id, clean_name, profile_id, controller_port, mixed_port,
            secrets.token_urlsafe(32), self._profile_mode(profile_id),
        )
        self._save_cores([*cores, core])
        return self._core_info(core)

    def update_core(self, core_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        cores = self.list_cores()
        current = next((core for core in cores if core.id == core_id), None)
        if current is None:
            raise ManagerError(f"core not found: {core_id}")
        profile_id = str(changes.get("profileId", current.profile_id))
        self._find_profile(profile_id)
        name = str(changes.get("name", current.name)).strip()
        if not name:
            raise ManagerError("core name is required")
        updated = ManagedCore(
            id=current.id,
            name=name,
            profile_id=profile_id,
            controller_port=int(changes.get("controllerPort", current.controller_port)),
            mixed_port=int(changes.get("mixedPort", current.mixed_port)),
            secret=current.secret,
            mode=self.normalize_core_mode(changes.get("mode", current.mode)),
            tun_enabled=bool(changes.get("tunEnabled", current.tun_enabled)),
            allow_lan=bool(changes.get("allowLan", current.allow_lan)),
            ipv6=bool(changes.get("ipv6", current.ipv6)),
            log_level=str(changes.get("logLevel", current.log_level)),
        )
        if updated.tun_enabled and not current.tun_enabled and os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
            raise ManagerError("开启 TUN 需要以管理员身份运行 OhMyClash")
        for port in (updated.controller_port, updated.mixed_port):
            if not 1024 <= port <= 65535:
                raise ManagerError("core ports must be between 1024 and 65535")
        if updated.controller_port == updated.mixed_port:
            raise ManagerError("controller and mixed ports must be different")
        used_ports = {
            port for core in cores if core.id != core_id
            for port in (core.controller_port, core.mixed_port)
        }
        if updated.controller_port in used_ports or updated.mixed_port in used_ports:
            raise ManagerError("core port is already used by another core")
        next_cores = [updated if core.id == core_id else core for core in cores]
        if updated.tun_enabled:
            next_cores = [
                core if core.id == core_id else ManagedCore(
                    id=core.id,
                    name=core.name,
                    profile_id=core.profile_id,
                    controller_port=core.controller_port,
                    mixed_port=core.mixed_port,
                    secret=core.secret,
                    mode=core.mode,
                    tun_enabled=False,
                    allow_lan=core.allow_lan,
                    ipv6=core.ipv6,
                    log_level=core.log_level,
                )
                for core in next_cores
            ]
        self._save_cores(next_cores)
        return self._core_info(updated)

    def update_core_mode(self, core_id: str, mode: object) -> dict[str, Any]:
        cores = self.list_cores()
        current = next((core for core in cores if core.id == core_id), None)
        if current is None:
            raise ManagerError(f"core not found: {core_id}")
        normalized = self.normalize_core_mode(mode)
        updated = ManagedCore(
            id=current.id,
            name=current.name,
            profile_id=current.profile_id,
            controller_port=current.controller_port,
            mixed_port=current.mixed_port,
            secret=current.secret,
            mode=normalized,
            tun_enabled=current.tun_enabled,
            allow_lan=current.allow_lan,
            ipv6=current.ipv6,
            log_level=current.log_level,
        )
        self._save_cores([updated if core.id == core_id else core for core in cores])
        return self._core_info(updated)

    def restore_cores(self, cores: list[ManagedCore]) -> None:
        """Restore a catalog snapshot after a runtime reload cannot start."""
        self._save_cores(cores)

    def delete_core(self, core_id: str) -> dict[str, Any]:
        cores = self.list_cores()
        core = next((item for item in cores if item.id == core_id), None)
        if core is None:
            raise ManagerError(f"core not found: {core_id}")
        runtime_dir = (self.instance_dir / core.id).resolve()
        instance_root = self.instance_dir.resolve()
        if runtime_dir.parent != instance_root:
            raise ManagerError("invalid core runtime directory")
        self._save_cores([item for item in cores if item.id != core_id])
        if runtime_dir.is_dir():
            shutil.rmtree(runtime_dir)
        return self._core_info(core)

    def list_profiles(self) -> list[ManagedProfile]:
        catalog = self._load_catalog()
        profiles: list[ManagedProfile] = []
        for path in sorted((*self.profile_dir.glob("*.yaml"), *self.profile_dir.glob("*.yml"))):
            profile_id = self._safe_id(path.stem)
            metadata = catalog.get(profile_id, {})
            profiles.append(
                ManagedProfile(
                    id=profile_id,
                    name=str(metadata.get("name") or path.stem),
                    source_path=path,
                    source_url=self._optional_string(metadata.get("sourceUrl")),
                    updated_at=self._optional_string(metadata.get("updatedAt")) or self._file_updated_at(path),
                )
            )
        return profiles

    def profile_infos(self) -> list[dict[str, Any]]:
        return [self._profile_info(profile) for profile in self.list_profiles()]

    def import_profile(self, name: str, content: str, source_url: str | None = None) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ManagerError("profile name is required")
        if len(clean_name) > 80:
            raise ManagerError("profile name is too long")
        if not content.strip():
            raise ManagerError("profile content is empty")
        if len(content.encode("utf-8")) > 20 * 1024 * 1024:
            raise ManagerError("profile content is larger than 20 MB")

        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ManagerError("profile YAML must contain an object")

        profile_id = self._profile_id_for_name(clean_name)
        path = self.profile_dir / f"{profile_id}.yaml"
        path.write_text(content, encoding="utf-8")
        now = self._now()
        catalog = self._load_catalog()
        catalog[profile_id] = {
            "name": clean_name,
            "sourceUrl": source_url,
            "updatedAt": now,
        }
        self._save_catalog(catalog)
        profile = ManagedProfile(profile_id, clean_name, path, source_url, now)
        return self._profile_info(profile)

    def add_subscription(self, name: str, url: str) -> dict[str, Any]:
        clean_url = self._validate_subscription_url(url)
        content = self._download_profile(clean_url)
        return self.import_profile(name, content, clean_url)

    def refresh_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self._find_profile(profile_id)
        if not profile.source_url:
            raise ManagerError("本地配置没有订阅地址，不能在线更新")
        content = self._download_profile(profile.source_url)
        return self.import_profile(profile.name, content, profile.source_url)

    def profile_content(self, profile_id: str) -> dict[str, Any]:
        profile = self._find_profile(profile_id)
        return {"profile": self._profile_info(profile), "content": profile.source_path.read_text(encoding="utf-8")}

    def update_profile(self, profile_id: str, name: str, content: str) -> dict[str, Any]:
        profile = self._find_profile(profile_id)
        clean_name = name.strip()
        if not clean_name:
            raise ManagerError("profile name is required")
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ManagerError("profile YAML must contain an object")
        profile.source_path.write_text(content, encoding="utf-8")
        catalog = self._load_catalog()
        metadata = dict(catalog.get(profile_id, {}))
        metadata["name"] = clean_name
        metadata["updatedAt"] = datetime.now(timezone.utc).isoformat()
        catalog[profile_id] = metadata
        self._save_catalog(catalog)
        return self._profile_info(self._find_profile(profile_id))

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self._find_profile(profile_id)
        users = [core.name for core in self.list_cores(migrate=True) if core.profile_id == profile_id]
        if users:
            raise ManagerError(f"配置正被核心使用: {', '.join(users)}")
        profile.source_path.unlink()
        catalog = self._load_catalog()
        catalog.pop(profile_id, None)
        self._save_catalog(catalog)
        return self._profile_info(profile)

    def reveal_profile(self, profile_id: str) -> None:
        profile = self._find_profile(profile_id)
        if os.name != "nt":
            raise ManagerError("show in folder is only supported on Windows")
        os.startfile(str(profile.source_path.parent))  # type: ignore[attr-defined]

    def _find_profile(self, profile_id: str) -> ManagedProfile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise ManagerError(f"profile not found: {profile_id}")

    def _profile_id_for_name(self, name: str) -> str:
        base = self._safe_id(name)
        catalog = self._load_catalog()
        existing = catalog.get(base)
        if existing and existing.get("name") != name:
            return f"{base}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"
        return base

    @staticmethod
    def _profile_info(profile: ManagedProfile) -> dict[str, Any]:
        return {
            "id": profile.id,
            "name": profile.name,
            "sourceType": "subscription" if profile.source_url else "local",
            "updatedAt": profile.updated_at,
        }

    def _load_catalog(self) -> dict[str, dict[str, Any]]:
        if not self.catalog_path.is_file():
            return {}
        try:
            value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(profile_id): metadata
            for profile_id, metadata in value.items()
            if isinstance(metadata, dict)
        }

    def _save_catalog(self, catalog: dict[str, dict[str, Any]]) -> None:
        self.catalog_path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _file_updated_at(path: Path) -> str | None:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            return None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_subscription_url(url: str) -> str:
        clean_url = url.strip()
        parsed = urlsplit(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ManagerError("订阅地址必须是 http 或 https URL")
        return clean_url

    @staticmethod
    def _download_profile(url: str) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/yaml, application/yaml, text/plain, */*",
                "User-Agent": "OhMyClash/0.1",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                content = response.read(20 * 1024 * 1024 + 1)
                charset = response.headers.get_content_charset() or "utf-8"
        except Exception as exc:  # noqa: BLE001
            raise ManagerError(f"订阅下载失败: {exc}") from exc
        if len(content) > 20 * 1024 * 1024:
            raise ManagerError("订阅内容大于 20 MB")
        try:
            return content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            try:
                return content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ManagerError("订阅内容不是可读取的 UTF-8/YAML 文本") from exc

    @staticmethod
    def _core_from_dict(raw: dict[str, Any]) -> ManagedCore:
        required = ("id", "name", "profileId", "controllerPort", "mixedPort", "secret")
        missing = [key for key in required if raw.get(key) in (None, "")]
        if missing:
            raise ManagerError(f"core missing required fields: {', '.join(missing)}")
        return ManagedCore(
            id=str(raw["id"]), name=str(raw["name"]), profile_id=str(raw["profileId"]),
            controller_port=int(raw["controllerPort"]), mixed_port=int(raw["mixedPort"]), secret=str(raw["secret"]),
            mode=ManagedRuntime.normalize_core_mode(raw.get("mode", "rule")),
            tun_enabled=bool(raw.get("tunEnabled", False)), allow_lan=bool(raw.get("allowLan", False)),
            ipv6=bool(raw.get("ipv6", False)), log_level=str(raw.get("logLevel", "info")),
        )

    @staticmethod
    def _core_info(core: ManagedCore) -> dict[str, Any]:
        return {
            "id": core.id, "name": core.name, "profileId": core.profile_id,
            "controllerPort": core.controller_port, "mixedPort": core.mixed_port,
            "mode": core.mode.title(),
            "tunEnabled": core.tun_enabled, "allowLan": core.allow_lan,
            "ipv6": core.ipv6, "logLevel": core.log_level,
        }

    def _save_cores(self, cores: list[ManagedCore]) -> None:
        self.cores_path.write_text(json.dumps([self._core_info(core) | {"secret": core.secret} for core in cores], ensure_ascii=False, indent=2), encoding="utf-8")

    def _unique_core_id(self, name: str, cores: list[ManagedCore]) -> str:
        base = self._safe_id(name).replace("profile-", "core-")
        used = {core.id for core in cores}
        if base not in used:
            return base
        index = 2
        while f"{base}-{index}" in used:
            index += 1
        return f"{base}-{index}"

    @staticmethod
    def normalize_core_mode(mode: object) -> str:
        normalized = str(mode).strip().lower()
        if normalized not in {"rule", "global", "direct", "script"}:
            raise ManagerError(f"unsupported core mode: {mode}")
        return normalized

    def _profile_mode(self, profile_id: str) -> str:
        profile = self._find_profile(profile_id)
        try:
            source = yaml.safe_load(profile.source_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ManagerError(f"cannot read profile mode: {exc}") from exc
        if not isinstance(source, dict):
            raise ManagerError(f"profile must contain a YAML object: {profile.source_path}")
        return self.normalize_core_mode(source.get("mode", "rule"))

    def _prepare_instance(self, core: ManagedCore, profile: ManagedProfile, core_path: Path) -> InstanceSpec:
        source = yaml.safe_load(profile.source_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            raise ManagerError(f"profile must contain a YAML object: {profile.source_path}")

        runtime_dir = self.instance_dir / core.id
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self._seed_geodata(runtime_dir)
        metadata_path = runtime_dir / "runtime.json"
        secret = core.secret
        controller_port = core.controller_port
        mixed_port = core.mixed_port

        generated = dict(source)
        generated["external-controller"] = f"127.0.0.1:{controller_port}"
        generated["secret"] = secret
        generated["mixed-port"] = mixed_port
        reserved_ports = {controller_port, mixed_port}
        for key in ("port", "socks-port", "redir-port", "tproxy-port"):
            if key in generated:
                generated[key] = self._free_port(reserved_ports)
                reserved_ports.add(generated[key])
        generated["allow-lan"] = core.allow_lan
        generated["ipv6"] = core.ipv6
        generated["log-level"] = core.log_level
        generated["mode"] = core.mode
        sniffer = dict(generated.get("sniffer") or {})
        sniffer["enable"] = True
        sniffer.setdefault("force-dns-mapping", True)
        sniffer.setdefault("parse-pure-ip", True)
        sniffer.setdefault("override-destination", False)
        sniffer.setdefault("sniff", {
            "HTTP": {"ports": [80, "8080-8880"]},
            "TLS": {"ports": [443, 8443]},
            "QUIC": {"ports": [443, 8443]},
        })
        generated["sniffer"] = sniffer
        tun = dict(generated.get("tun") or {})
        tun["enable"] = core.tun_enabled
        if core.tun_enabled:
            tun.setdefault("stack", "mixed")
            tun.setdefault("auto-route", True)
            tun.setdefault("auto-detect-interface", True)
            if os.name == "nt" and not generated.get("interface-name"):
                generated["interface-name"] = self._windows_physical_default_interface()
            configured_exclusions = tun.get("route-exclude-address") or []
            if not isinstance(configured_exclusions, list):
                raise ManagerError("tun.route-exclude-address must be an array")
            tun["route-exclude-address"] = list(dict.fromkeys([
                *(str(item) for item in configured_exclusions),
                *self._proxy_endpoint_exclusions(generated),
            ]))
            bypass_rules = self._local_proxy_process_rules(generated)
            configured_rules = generated.get("rules") or []
            if not isinstance(configured_rules, list):
                raise ManagerError("rules must be an array")
            generated["rules"] = list(dict.fromkeys([*bypass_rules, *(str(item) for item in configured_rules)]))
        generated["tun"] = tun

        config_path = runtime_dir / "config.yaml"
        config_path.write_text(yaml.safe_dump(generated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        metadata_path.write_text(
            json.dumps(
                {
                    "profileId": profile.id,
                    "name": core.name,
                    "controllerPort": controller_port,
                    "mixedPort": mixed_port,
                    "secret": secret,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return InstanceSpec(
            id=core.id,
            name=core.name,
            core_path=core_path,
            home_dir=runtime_dir,
            config_path=config_path,
            controller_url=f"http://127.0.0.1:{controller_port}",
            secret=secret,
        )

    @staticmethod
    def _proxy_endpoint_exclusions(config: dict[str, Any]) -> list[str]:
        """Keep literal proxy endpoints outside TUN routes to prevent traffic loops."""
        proxies = config.get("proxies") or []
        if not isinstance(proxies, list):
            return []
        exclusions: list[str] = []
        for proxy in proxies:
            if not isinstance(proxy, dict):
                continue
            server = proxy.get("server")
            if not isinstance(server, str):
                continue
            try:
                address = ipaddress.ip_address(server.strip())
            except ValueError:
                continue
            exclusions.append(f"{address}/{address.max_prefixlen}")
        return list(dict.fromkeys(exclusions))

    def _seed_geodata(self, runtime_dir: Path) -> None:
        source = self.project_root / "runtime" / "geodata" / "geoip.metadb"
        if not source.is_file():
            raise ManagerError(f"内置 GeoIP 数据库缺失: {source}")
        if self._file_sha256(source) != BUNDLED_GEOIP_METADB_SHA256:
            raise ManagerError(f"内置 GeoIP 数据库校验失败: {source}")

        target = runtime_dir / "geoip.metadb"
        if target.is_file() and self._file_sha256(target) == BUNDLED_GEOIP_METADB_SHA256:
            return

        temporary = runtime_dir / "geoip.metadb.tmp"
        shutil.copy2(source, temporary)
        temporary.replace(target)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _windows_physical_default_interface() -> str:
        command = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "$routes=Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop | "
            "Where-Object {$_.NextHop -ne '198.18.0.2' -and $_.InterfaceAlias -ne 'Meta'}; "
            "$route=$routes | Sort-Object RouteMetric | Select-Object -First 1; "
            "if ($null -eq $route) { throw 'physical default interface not found' }; "
            "$route.InterfaceAlias"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
            check=False,
        )
        interface_name = result.stdout.strip()
        if result.returncode != 0 or not interface_name:
            detail = result.stderr.strip() or "physical default interface not found"
            raise ManagerError(f"无法确定 TUN 出站网卡: {detail}")
        return interface_name

    @staticmethod
    def _local_proxy_process_rules(config: dict[str, Any]) -> list[str]:
        if os.name != "nt":
            return []
        proxies = config.get("proxies") or []
        if not isinstance(proxies, list):
            return []
        local_ports: set[int] = set()
        for proxy in proxies:
            if not isinstance(proxy, dict):
                continue
            server = str(proxy.get("server", "")).strip().lower()
            if server not in {"127.0.0.1", "::1", "localhost"}:
                continue
            try:
                port = int(proxy.get("port"))
            except (TypeError, ValueError):
                raise ManagerError(f"本地代理 {proxy.get('name', '<unnamed>')} 缺少有效端口")
            if not 1 <= port <= 65535:
                raise ManagerError(f"本地代理 {proxy.get('name', '<unnamed>')} 端口无效: {port}")
            local_ports.add(port)

        rules: list[str] = []
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for port in sorted(local_ports):
            command = (
                f"$connection=Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | "
                "Select-Object -First 1; "
                "if ($null -ne $connection) {(Get-Process -Id $connection.OwningProcess -ErrorAction Stop).ProcessName}"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=creationflags,
                check=False,
            )
            process_name = result.stdout.strip()
            if result.returncode != 0 or not process_name:
                raise ManagerError(f"本地上游代理 127.0.0.1:{port} 没有正在监听的进程，无法安全开启 TUN")
            executable = process_name if process_name.lower().endswith(".exe") else f"{process_name}.exe"
            rules.append(f"PROCESS-NAME,{executable},DIRECT")
        return list(dict.fromkeys(rules))

    @staticmethod
    def _load_metadata(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _safe_id(value: str) -> str:
        result = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_").lower()
        if not result:
            result = f"profile-{hashlib.sha1(value.encode('utf-8')).hexdigest()[:10]}"
        return result

    @staticmethod
    def _free_port(excluded: set[int] | None = None) -> int:
        excluded = excluded or set()
        for _ in range(20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = int(probe.getsockname()[1])
            if port not in excluded:
                return port
        raise ManagerError("could not allocate a free local port")


def create_managed_runtime(project_root: Path) -> ManagedRuntime:
    return ManagedRuntime(project_root)


def create_managed_manager(project_root: Path) -> MihomoManager:
    return create_managed_runtime(project_root).build_manager()
