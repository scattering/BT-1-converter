#!/usr/bin/env python3
"""Serve the local browser harness and save bounded same-origin test evidence."""
from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_REPORT_BYTES = 32 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, "Directory listings are disabled")
        return None

    def _valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_head(self):
        if not self._valid_host():
            self.send_error(403, "Invalid host")
            return None
        requested = unquote(urlsplit(self.path).path)
        path = (ROOT / requested.lstrip("/")).resolve()
        if not path.is_relative_to(ROOT):
            self.send_error(403)
            return None
        relative = path.relative_to(ROOT)
        parts = relative.parts
        allowed = (
            len(parts) >= 2 and parts[0] == "browser" and path.suffix in {".html", ".mjs", ".js", ".css", ".py"}
            or len(parts) == 2 and parts[0] in {"bt1modern", "tests"} and path.suffix == ".py"
            or len(parts) >= 4 and parts[:2] == ("artifacts", "pyodide-runtime")
            and path.suffix in {".mjs", ".js", ".wasm", ".zip", ".whl", ".json"}
            or len(parts) >= 3 and parts[:2] == ("artifacts", "pyodide-validation")
            and (parts[2] == "inputs" or len(parts) == 3 and path.suffix == ".json")
        )
        if requested in {"/", "/browser/"}:
            self.path = "/browser/index.html"
            allowed = True
        if not allowed:
            self.send_error(403, "Path is outside browser test assets")
            return None
        return super().send_head()

    def do_POST(self):
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if (self.path != "/__evidence" or not self._valid_host()
                or origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
                or self.headers.get_content_type() != "application/json"):
            self.send_error(403, "Only same-origin JSON evidence is accepted")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REPORT_BYTES:
                raise ValueError("Invalid evidence size")
            def reject_constant(value):
                raise ValueError(f"Non-finite JSON constant: {value}")

            report = json.loads(self.rfile.read(length), parse_constant=reject_constant)
            filenames = {"filesystem": "filesystem.json", "validation": "browser-validation.json",
                         "upload": "browser-upload.json", "portable": "browser-portable.json"}
            filename = filenames[report["kind"]]
            if not isinstance(report.get("report"), dict):
                raise ValueError("Evidence report must be an object")
            serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
        except (ValueError, KeyError, TypeError) as error:
            self.send_error(400, str(error))
            return
        destination = ROOT / "artifacts" / "pyodide-validation" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent,
                                         prefix=filename + ".", suffix=".partial", delete=False) as handle:
            handle.write(serialized)
            temporary = Path(handle.name)
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        data = json.dumps({"saved": str(destination.relative_to(ROOT))}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"BT-1 browser harness: http://127.0.0.1:{args.port}/browser/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
