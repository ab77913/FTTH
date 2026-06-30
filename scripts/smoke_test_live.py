"""Live HTTP smoke test — run while api_server.py is listening on port 8000."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    data: dict | None = None,
    files: dict | None = None,
    timeout: int = 60,
):
    url = BASE + path
    headers: dict[str, str] = {}
    body: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if files:
        boundary = uuid.uuid4().hex
        chunks: list[bytes] = []
        for field, (fname, content, ctype) in files.items():
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
            )
            chunks.append(content)
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(raw)
        return resp.status, raw.decode("utf-8", errors="replace")


def main() -> int:
    passed = failed = 0

    def check(name: str, fn) -> None:
        nonlocal passed, failed
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {name} — {exc}")
            failed += 1

    check("GET /api/health", lambda: _request("GET", "/api/health")[1]["status"] == "ok")

    def ready_ok():
        _, body = _request("GET", "/api/ready", timeout=15)
        assert "checks" in body

    check("GET /api/ready", ready_ok)

    def frontend_shell():
        _, html = _request("GET", "/")
        assert "/assets/app/bootstrap.jsx" in html
        assert "/assets/api/auth.js" in html

    check("GET / frontend shell", frontend_shell)

    def auth_js():
        _, text = _request("GET", "/assets/api/auth.js")
        assert "apiFetch" in text
        assert "global.FTTH" in text or "FTTH" in text

    check("GET /assets/api/auth.js", auth_js)

    _, login = _request(
        "POST",
        "/api/login",
        data={"username": "ftth_team", "password": "Meridian@2026"},
    )
    token = login["token"]
    check("POST /api/login", lambda: token)

    _, jobs = _request("GET", "/api/jobs", token=token)
    check("GET /api/jobs", lambda: isinstance(jobs, list))

    if jobs:
        job_id = jobs[0]["id"]
        check(
            "GET /api/jobs/{id}",
            lambda: _request("GET", f"/api/jobs/{job_id}", token=token)[1]["id"] == job_id,
        )

    check(
        "GET /api/jobs unauthorized",
        lambda: _expect_http(401, "GET", "/api/jobs"),
    )

    csv = ROOT / "examples" / "sample_addresses.csv"
    if csv.is_file():
        upload_body = _request(
            "POST",
            "/api/upload",
            token=token,
            files={"file": (csv.name, csv.read_bytes(), "text/csv")},
        )[1]
        check("POST /api/upload CSV", lambda: upload_body.get("job_id"))
        new_job = upload_body.get("job_id")
        if new_job:
            rec_body = _request("GET", f"/api/records?job_id={new_job}&limit=3", token=token)[1]
            check("GET /api/records for new job", lambda: "records" in rec_body)

    check(
        "GET /api/maps-key",
        lambda: "key" in _request("GET", "/api/maps-key", token=token)[1],
    )

    print(f"\n=== Live smoke: {passed} passed, {failed} failed ===")
    return 1 if failed else 0


def _expect_http(code: int, method: str, path: str) -> None:
    try:
        _request(method, path)
        raise AssertionError(f"expected HTTP {code}")
    except HTTPError as exc:
        if exc.code != code:
            raise AssertionError(f"expected HTTP {code}, got {exc.code}") from exc


if __name__ == "__main__":
    sys.exit(main())
