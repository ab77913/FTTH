# Ollama Chat – User Guide

## Overview

A local network chat server exposing all your Ollama models via:
- **Web UI** — Telegram-style messaging app with file attachment support
- **REST API** — For developers to integrate from any language or tool

---

## 1. Starting the Server

If you want to run this chat app together with the FTTH app on the same machine, use:

```
launch_both_apps.bat
```

That launcher starts FTTH first, then starts Ollama Chat separately on `http://172.19.64.7:8082/`.

Double-click `start_server.bat`.

The launcher will:
- request Administrator permission so Windows Firewall can allow office laptops
- start Flask privately on `http://127.0.0.1:5100`
- install nginx automatically if it is missing
- start nginx on port `8082`
- allow only the detected private office network range, for example `172.16.0.0/12`
- print the office URL, for example `http://172.19.64.7:8082/`

People on the office network can open the printed Office URL in their browser.
People outside the office subnet are blocked by nginx.

For direct local-only development without nginx, run:
```
python server.py
```

The terminal shows your network address:
```
  UI:      http://localhost:5100
  Network: http://172.19.64.7:5100
  API:     http://172.19.64.7:5100/api/v1/
```

For normal office use, share the Office URL printed by `start_server.bat`, not the private development URL.

---

## 2. Web Chat UI

### Starting a chat
1. Click **+** (top of sidebar) to create a new conversation
2. Select a model from the dropdown in the top bar
3. Type your message and press **Enter** (or click the arrow)
4. Press **Shift+Enter** for a new line without sending

### Attaching files
1. Click the **📎 paperclip** button in the message bar
2. Select any file — image, PDF, Word doc, spreadsheet, code file, etc.
3. A preview appears above the text input (image thumbnail or file chip)
4. Type your question and send — the file goes with the message
5. Click **✕** on the preview to remove before sending

### Supported attachments
| Type | Formats | How the LLM sees it |
|------|---------|---------------------|
| Images | PNG, JPG, JPEG, GIF, WebP, BMP | Sent as vision input |
| Documents | PDF, DOCX, DOC | Text is extracted and included |
| Spreadsheets | XLSX, XLS, CSV | Rows read and included as text |
| Code & text | py, js, ts, java, go, sql, json, yaml, sh, txt, md, … | Full content included |

> **Vision models**: Use `qwen2.5vl` or `moondream` for image analysis.
> **Code**: Use `deepseek-coder:33b` or `qwen2.5-coder:14b` for best results.

### Managing conversations
- All conversations are **saved automatically** — history persists across restarts
- Click any conversation in the sidebar to resume it
- Hover over a sidebar item → click the **trash icon** to delete
- Use the **header trash icon** to delete the current conversation
- Use the **search bar** to find old conversations

---

## 3. REST API Reference

Base URL: `http://172.19.64.7:8082/api/v1/`

Full interactive docs: `http://172.19.64.7:8082/api/v1/docs`

### Quick reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Check server status |
| GET | `/api/v1/models` | List all available models |
| POST | `/api/v1/chat` | Send a message (text or file) |
| GET | `/api/v1/conversations` | List all conversations |
| POST | `/api/v1/conversations` | Create a new conversation |
| GET | `/api/v1/conversations/{id}` | Get conversation + messages |
| PATCH | `/api/v1/conversations/{id}` | Rename / change model |
| DELETE | `/api/v1/conversations/{id}` | Delete conversation |

### Text chat (curl)
```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3:latest","message":"What is Python?"}'
```

Response:
```json
{
  "success": true,
  "response": "Python is a high-level, interpreted programming language...",
  "model": "llama3:latest",
  "conversation_id": 42
}
```

### Image analysis (curl)
```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5vl:latest" \
  -F "message=What is in this image?" \
  -F "file=@photo.jpg"
```

### PDF / document analysis
```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5:14b" \
  -F "message=Summarize the key findings." \
  -F "file=@report.pdf"
```

### Code review
```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=deepseek-coder:33b" \
  -F "message=Find bugs and suggest improvements." \
  -F "file=@app.py"
```

### Multi-turn conversation
```bash
# First message — returns conversation_id
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3:latest","message":"My name is Alice."}'
# → {"conversation_id": 5, "response": "Nice to meet you, Alice!"}

# Follow-up using the same conversation
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3:latest","message":"What is my name?","conversation_id":5}'
# → {"response": "Your name is Alice."}
```

### Streaming response
```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3:latest","message":"Write a poem","stream":true}'
```

Each line is a JSON object. The first line is a metadata packet with `conversation_id`.

### Python example
```python
import requests

BASE = "http://172.19.64.7:8082/api/v1"

# Simple text
r = requests.post(f"{BASE}/chat", json={
    "model": "llama3:latest",
    "message": "Explain REST APIs"
})
print(r.json()["response"])

# Image
with open("chart.png", "rb") as f:
    r = requests.post(f"{BASE}/chat", files={"file": f}, data={
        "model": "qwen2.5vl:latest",
        "message": "What does this chart show?"
    })
print(r.json()["response"])
```

---

## 4. Setting Up Nginx (optional)

Nginx exposes the chat app on `:8082` so it does not conflict with the FTTH app on port `80`.

### Install nginx on Windows
1. Download from https://nginx.org/en/download.html (Stable version)
2. Extract to `C:\nginx`
3. Copy `nginx\ollama-chat.conf` from this folder to `C:\nginx\conf\nginx.conf`
4. Edit the `uploads` path in the config to match your actual path

### Start nginx
```
C:\nginx\nginx.exe
```

### Verify it works
Open `http://172.19.64.7:8082/` in a browser - should show the chat UI on port 8082.

### Commands
```
C:\nginx\nginx.exe           # start
C:\nginx\nginx.exe -s reload # reload config (after changes)
C:\nginx\nginx.exe -s stop   # stop
```

---

## 5. Model Recommendations

| Task | Best Model |
|------|-----------|
| General chat | `llama3:latest` or `qwen2.5:14b` |
| Image analysis | `qwen2.5vl:latest` or `moondream` |
| Code review / generation | `deepseek-coder:33b` or `qwen2.5-coder:14b` |
| Large documents | `qwen2.5:32b` (more context) |
| Fast responses | `llama3.2:3b` or `mistral:7b` |
| Complex reasoning | `deepseek-r1:8b` or `qwen3:30b` |

---

## 6. Files & Data

| File | Purpose |
|------|---------|
| `chat_history.db` | SQLite database — all conversations and messages |
| `uploads/` | Uploaded files (images, PDFs, etc.) |
| `server.py` | Main server |
| `templates/index.html` | Chat web UI |
| `templates/api_docs.html` | API documentation page |
| `nginx/ollama-chat.conf` | Nginx configuration |

To back up all history: copy `chat_history.db` and the `uploads/` folder.
