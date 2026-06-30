"""Client helper for extracting text from files through the FTTH Ollama API.

Examples:
    python examples/ollama_text_extraction_client.py photo.jpg --host http://192.168.1.50:8000

    python examples/ollama_text_extraction_client.py photo.jpg --host http://192.168.1.50:8000 --output text.txt

    python examples/ollama_text_extraction_client.py photo.jpg --host http://192.168.1.50:8000 \
        --username developer1 --password Password123

Environment variables are also supported:
    FTTH_HOST=http://192.168.1.50:8000
    OLLAMA_MODEL=qwen2.5vl:latest
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests


DEFAULT_HOST = os.environ.get("FTTH_HOST", "http://172.19.64.7:8000").rstrip("/")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:latest")
PUBLIC_ENDPOINT = "/api/ollama/public/extract-text"
AUTH_ENDPOINT = "/api/ollama/v1/chat"
DEFAULT_MESSAGE = (
    "Extract all visible text from this file. "
    "For images, read signs, labels, house numbers, documents, and handwritten or low-quality text if visible. "
    "Return the extracted text first, then a short confidence note."
)


def login(host: str, username: str, password: str, timeout: int = 30) -> str:
    """Sign in to the FTTH host and return a JWT token."""
    response = requests.post(
        f"{host.rstrip('/')}/api/login",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    token = response.json().get("token")
    if not token:
        raise RuntimeError("Login succeeded but no token was returned.")
    return str(token)


def extract_text_from_file(
    file_path: str | Path,
    *,
    host: str = DEFAULT_HOST,
    token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    authenticated: bool = False,
    model: str = DEFAULT_MODEL,
    message: str = DEFAULT_MESSAGE,
    stream: bool = False,
    conversation_id: int | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    """Upload a file to the FTTH Ollama API and return the full JSON response.

    By default this uses the public endpoint and does not require a token,
    username, or password. Set `authenticated=True` to use account chat history.

    The response usually contains:
        - success
        - response
        - model
        - conversation_id
    """
    host = host.rstrip("/")
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)

    data: dict[str, str] = {
        "model": model,
        "message": message,
        "stream": "true" if stream else "false",
    }
    if authenticated and conversation_id is not None:
        data["conversation_id"] = str(conversation_id)

    headers: dict[str, str] = {}
    endpoint = PUBLIC_ENDPOINT
    if authenticated:
        auth_token = token or os.environ.get("FTTH_TOKEN")
        if not auth_token:
            username = username or os.environ.get("FTTH_USERNAME")
            password = password or os.environ.get("FTTH_PASSWORD")
            if not username or not password:
                raise ValueError("Authenticated mode needs --token or --username and --password.")
            auth_token = login(host, username, password, timeout=timeout)
        headers["Authorization"] = f"Bearer {auth_token}"
        endpoint = AUTH_ENDPOINT

    with path.open("rb") as file_obj:
        response = requests.post(
            f"{host}{endpoint}",
            headers=headers,
            data=data,
            files={"file": (path.name, file_obj)},
            timeout=timeout,
        )

    response.raise_for_status()
    if stream:
        return {"success": True, "stream": response.text}
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text from a file using the FTTH Ollama API.")
    parser.add_argument("file", help="Path to image, PDF, document, spreadsheet, code, or text file")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"FTTH host URL, default: {DEFAULT_HOST}")
    parser.add_argument("--authenticated", action="store_true", help="Use protected account chat API instead of the public extraction API")
    parser.add_argument("--token", default=os.environ.get("FTTH_TOKEN"), help="JWT token from /api/login for --authenticated mode")
    parser.add_argument("--username", default=os.environ.get("FTTH_USERNAME"), help="FTTH username for --authenticated mode")
    parser.add_argument("--password", default=os.environ.get("FTTH_PASSWORD"), help="FTTH password for --authenticated mode")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model, default: {DEFAULT_MODEL}")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="Prompt to send with the file")
    parser.add_argument("--conversation-id", type=int, default=None, help="Continue an existing chat conversation in --authenticated mode")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", default="", help="Optional path to save extracted response text")
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of only the extracted response")
    args = parser.parse_args()

    result = extract_text_from_file(
        args.file,
        host=args.host,
        token=args.token,
        username=args.username,
        password=args.password,
        authenticated=args.authenticated,
        model=args.model,
        message=args.message,
        conversation_id=args.conversation_id,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result.get("response", ""))
        if result.get("conversation_id") is not None:
            print(f"\nConversation ID: {result['conversation_id']}")

    if args.output:
        Path(args.output).write_text(str(result.get("response", "")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
