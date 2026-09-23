from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
RUNTIME_FILES = (
    Path("runtime") / "geodata" / "geoip.metadb",
    Path("runtime") / "core" / "mihomo.exe",
)
EXCLUDED_PARTS = {".git", "__pycache__", "data"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_runtime() -> dict[Path, str]:
    hashes: dict[Path, str] = {}
    for relative_path in RUNTIME_FILES:
        source = ROOT / relative_path
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError(f"required runtime resource is missing or empty: {source}")
        hashes[relative_path] = sha256(source)
    return hashes


def pe_subsystem(executable: bytes) -> tuple[int, int]:
    if executable[:2] != b"MZ":
        raise ValueError("packaged application is not a Windows executable")
    pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
    if executable[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("packaged application has an invalid PE header")
    machine = struct.unpack_from("<H", executable, pe_offset + 4)[0]
    subsystem = struct.unpack_from("<H", executable, pe_offset + 24 + 68)[0]
    return machine, subsystem


def should_exclude(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS or part.endswith((".pyc", ".pyo")) for part in path.parts)


def build_archive(app_dir: Path, archive_path: Path, expected_hashes: dict[Path, str]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(app_dir)
            if should_exclude(relative):
                continue
            archive.write(path, Path("OhMyClash") / relative)

    with zipfile.ZipFile(archive_path) as archive:
        damaged_entry = archive.testzip()
        if damaged_entry is not None:
            raise RuntimeError(f"archive CRC check failed for {damaged_entry}")
        names = set(archive.namelist())
        for relative_path, expected_hash in expected_hashes.items():
            name = str(Path("OhMyClash") / relative_path).replace("\\", "/")
            try:
                contents = archive.read(name)
            except KeyError as exc:
                raise RuntimeError(f"package is missing required runtime resource: {name}") from exc
            actual_hash = hashlib.sha256(contents).hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"packaged runtime resource hash differs from source: {name}")

        executable = "OhMyClash/OhMyClash.exe"
        if executable not in names:
            raise RuntimeError("package is missing OhMyClash.exe")
        machine, subsystem = pe_subsystem(archive.read(executable))
        if machine != 0x8664 or subsystem != 2:
            raise RuntimeError(f"expected a 64-bit GUI executable, found machine=0x{machine:04X}, subsystem={subsystem}")

        unexpected = [name for name in names if ".git/" in name or "/__pycache__/" in name or name.endswith((".pyc", ".pyo"))]
        if unexpected:
            raise RuntimeError(f"package contains excluded development files: {unexpected[:5]}")


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Windows packaging must be run on Windows")

    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = package["version"]
    archive_path = OUTPUT_DIR / f"OhMyClash-v{version}-windows-x64.zip"
    if archive_path.exists():
        raise FileExistsError(f"refusing to overwrite an existing release archive: {archive_path}")

    expected_hashes = validate_runtime()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".package-v{version}-", dir=OUTPUT_DIR) as temporary:
        work_dir = Path(temporary)
        spec_dir = work_dir / "spec"
        dist_dir = work_dir / "dist"
        build_dir = work_dir / "build"
        spec_dir.mkdir()

        subprocess.run(["npm.cmd", "run", "build"], cwd=ROOT, check=True)

        makespec_args = [
            sys.executable,
            "-I",
            "-m",
            "PyInstaller.utils.cliutils.makespec",
            "--windowed",
            "--onedir",
            "--contents-directory",
            ".",
            "--name",
            "OhMyClash",
            "--specpath",
            str(spec_dir),
            "--add-data",
            f"{ROOT / 'dist'};dist",
            "--add-data",
            f"{ROOT / 'WebViewUI'};WebViewUI",
            "--add-data",
            f"{ROOT / 'runtime'};runtime",
        ]
        for module in ("pystray", "PIL", "yaml", "pythonnet", "clr_loader"):
            makespec_args.extend(("--collect-all", module))
        makespec_args.extend(
            (
                "--hidden-import",
                "webview.platforms.edgechromium",
                "--hidden-import",
                "webview.platforms.winforms",
                "--exclude-module",
                "webview.platforms.android",
                "--exclude-module",
                "webview.platforms.cocoa",
                "--exclude-module",
                "webview.platforms.gtk",
                "--exclude-module",
                "webview.platforms.qt",
                "--exclude-module",
                "webview.platforms.cef",
                str(ROOT / "desktop" / "launcher.py"),
            )
        )
        subprocess.run(makespec_args, cwd=ROOT, check=True)
        spec_path = spec_dir / "OhMyClash.spec"
        spec_contents = spec_path.read_text(encoding="utf-8-sig")
        isolated_site = work_dir / "isolated-user-site"
        spec_path.write_text(
            f"import site\nsite.getusersitepackages = lambda: {str(isolated_site)!r}\n" + spec_contents,
            encoding="utf-8",
        )

        pyinstaller_args = [
            sys.executable,
            "-I",
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(build_dir),
            str(spec_path),
        ]
        subprocess.run(pyinstaller_args, cwd=ROOT, check=True)

        app_dir = dist_dir / "OhMyClash"
        for relative_path in RUNTIME_FILES:
            packaged_file = app_dir / relative_path
            if not packaged_file.is_file():
                raise RuntimeError(f"PyInstaller output is missing required runtime resource: {packaged_file}")

        with tempfile.NamedTemporaryFile(prefix=".OhMyClash-release-", suffix=".zip", dir=OUTPUT_DIR, delete=False) as temporary_archive:
            temporary_archive_path = Path(temporary_archive.name)
        try:
            build_archive(app_dir, temporary_archive_path, expected_hashes)
            shutil.move(str(temporary_archive_path), str(archive_path))
        finally:
            temporary_archive_path.unlink(missing_ok=True)

    print(f"Package: {archive_path}")
    print(f"Size: {archive_path.stat().st_size} bytes")
    print(f"SHA-256: {sha256(archive_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
