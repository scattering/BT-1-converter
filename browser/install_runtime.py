#!/usr/bin/env python3
"""Install the pinned official Pyodide core and matching NumPy, locally only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

VERSION = "314.0.7"
CORE_SHA256 = "2abdcc2e35208af406e07724cffa85bc582ced97e9028383ecf5462541393f95"
ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, path: Path, expected: str | None = None) -> dict:
    if not path.exists() or (expected and sha256(path) != expected):
        request = urllib.request.Request(url, headers={"User-Agent": "bt1-pyodide-validation"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            raise ValueError(f"SHA-256 mismatch for {url}: {digest}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        temporary.write_bytes(data)
        temporary.replace(path)
    digest = sha256(path)
    if expected and digest != expected:
        raise ValueError(f"SHA-256 mismatch for cached {path}")
    return {"url": url, "file": path.name, "sha256": digest, "bytes": path.stat().st_size,
            "expected_sha256": expected}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path,
                        default=ROOT / "artifacts" / "pyodide-runtime" / VERSION)
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    archive_name = f"pyodide-core-{VERSION}.tar.bz2"
    archive = destination / archive_name
    url = f"https://github.com/pyodide/pyodide/releases/download/{VERSION}/{archive_name}"
    core = download(url, archive, CORE_SHA256)
    with tarfile.open(archive, "r:bz2") as bundle:
        for item in bundle.getmembers():
            target = (destination / item.name).resolve()
            if not target.is_relative_to(destination) or not (item.isfile() or item.isdir()):
                raise ValueError(f"Unsafe runtime archive member: {item.name}")
        bundle.extractall(destination, filter="data")
    runtime = destination / "pyodide"
    lock_path = runtime / "pyodide-lock.json"
    lock = json.loads(lock_path.read_text())
    packages = []
    visited = set()

    def install(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        package = lock["packages"][name]
        for dependency in package["depends"]:
            install(dependency)
        filename = package["file_name"]
        if Path(filename).name != filename:
            raise ValueError(f"Unexpected package filename: {filename}")
        result = download(f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/{filename}",
                          runtime / filename, package["sha256"])
        packages.append({"name": name, "version": package["version"], **result})

    install("numpy")
    provenance = {"pyodide_version": VERSION, "lock_info": lock["info"], "core": core,
                  "lock_sha256": sha256(lock_path), "packages": packages,
                  "runtime_directory": str(runtime),
                  "documentation": "https://pyodide.org/en/stable/usage/downloading-and-deploying.html"}
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
