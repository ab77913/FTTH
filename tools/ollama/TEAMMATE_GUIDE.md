# Office Ollama Chatbot and API Guide

This server gives office teammates access to local Ollama models through:

- Web chatbot UI
- REST API for development
- Text, document, code, spreadsheet, and image/vision prompts

## Access

From the FTTH app, click the `AI` button near the settings icon to open the chatbot.

Use this URL while connected to the office network:

```text
http://172.19.64.7:8082/
```

API base URL:

```text
http://172.19.64.7:8082/api/v1
```

API docs in the browser:

```text
http://172.19.64.7:8082/api/v1/docs
```

If you see `403 Forbidden`, you are not coming from an allowed office network address. Connect to the office network or VPN and try again.

## Use the Chatbot UI

1. Open `http://172.19.64.7:8082/`.
2. Click the `+` button to start a new chat.
3. Choose a model from the model dropdown.
4. Type a message and press Enter.
5. Use the attachment button to upload images, PDFs, Word files, spreadsheets, code files, or text files.

For image analysis, choose a vision model such as:

- `qwen2.5vl:latest`
- `moondream:latest`

For code tasks, choose a coding model such as:

- `deepseek-coder:33b`
- `qwen2.5-coder:14b`

Available model names can change. Check the live model list here:

```text
http://172.19.64.7:8082/api/v1/models
```

## API Quick Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/health` | Check server and Ollama status |
| GET | `/api/v1/models` | List available models |
| POST | `/api/v1/chat` | Send text or file prompt |
| GET | `/api/v1/conversations` | List saved conversations |
| GET | `/api/v1/conversations/{id}` | Get one conversation |
| POST | `/api/v1/conversations` | Create a conversation |
| PATCH | `/api/v1/conversations/{id}` | Rename/change model |
| DELETE | `/api/v1/conversations/{id}` | Delete a conversation |

## Text Chat API

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3:latest\",\"message\":\"Explain REST APIs in simple terms.\"}"
```

Example response:

```json
{
  "success": true,
  "response": "REST APIs let applications communicate over HTTP...",
  "model": "llama3:latest",
  "conversation_id": 42
}
```

## Use Any Model

First list models:

```bash
curl http://172.19.64.7:8082/api/v1/models
```

Then pass the exact model name in the `model` field:

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"qwen2.5:14b\",\"message\":\"Summarize the benefits of local LLMs.\"}"
```

## Vision LLM / Image API

Use multipart form data and attach the image as `file`.

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5vl:latest" \
  -F "message=Describe this image and extract any visible text." \
  -F "file=@photo.jpg"
```

Other image formats also work, including PNG, JPEG, WebP, GIF, BMP, and TIFF.

## Document Analysis API

PDF:

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5:14b" \
  -F "message=Summarize this document and list the key action items." \
  -F "file=@report.pdf"
```

Word document:

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5:14b" \
  -F "message=Extract the important points from this document." \
  -F "file=@notes.docx"
```

Spreadsheet or CSV:

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=qwen2.5:14b" \
  -F "message=Analyze this data and highlight trends." \
  -F "file=@data.xlsx"
```

## Code Review API

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -F "model=deepseek-coder:33b" \
  -F "message=Review this code for bugs, security issues, and performance improvements." \
  -F "file=@app.py"
```

## Continue a Conversation

The first chat response includes `conversation_id`. Reuse it to keep context.

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3:latest\",\"message\":\"My project uses Flask.\",\"stream\":false}"
```

Then:

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3:latest\",\"message\":\"What framework did I say I use?\",\"conversation_id\":42}"
```

## Streaming Responses

Set `stream` to `true`. The response is newline-delimited JSON.

```bash
curl -X POST http://172.19.64.7:8082/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3:latest\",\"message\":\"Write a short story.\",\"stream\":true}"
```

The first line contains metadata with the conversation ID. Later lines contain model tokens.

## Python Examples

Install requests if needed:

```bash
pip install requests
```

Text chat:

```python
import requests

BASE = "http://172.19.64.7:8082/api/v1"

response = requests.post(f"{BASE}/chat", json={
    "model": "llama3:latest",
    "message": "Explain APIs in two paragraphs."
})

data = response.json()
print(data["response"])
print("Conversation ID:", data["conversation_id"])
```

Image analysis:

```python
import requests

BASE = "http://172.19.64.7:8082/api/v1"

with open("photo.jpg", "rb") as image:
    response = requests.post(
        f"{BASE}/chat",
        data={
            "model": "qwen2.5vl:latest",
            "message": "Describe this image in detail."
        },
        files={"file": image}
    )

print(response.json()["response"])
```

Continue a conversation:

```python
import requests

BASE = "http://172.19.64.7:8082/api/v1"
conversation_id = None

for message in ["My name is Priya.", "What is my name?"]:
    payload = {
        "model": "llama3:latest",
        "message": message
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    response = requests.post(f"{BASE}/chat", json=payload)
    data = response.json()
    conversation_id = data["conversation_id"]
    print(data["response"])
```

## JavaScript Example

```javascript
const BASE = "http://172.19.64.7:8082/api/v1";

async function chat() {
  const response = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "llama3:latest",
      message: "Give me three ideas for automating reports."
    })
  });

  const data = await response.json();
  console.log(data.response);
}

chat();
```

Image upload from browser or Node-style `FormData`:

```javascript
const BASE = "http://172.19.64.7:8082/api/v1";
const form = new FormData();

form.append("model", "qwen2.5vl:latest");
form.append("message", "What is shown in this image?");
form.append("file", fileInput.files[0]);

const response = await fetch(`${BASE}/chat`, {
  method: "POST",
  body: form
});

const data = await response.json();
console.log(data.response);
```

## Supported File Types

| Type | Formats |
|------|---------|
| Images | PNG, JPG, JPEG, GIF, WebP, BMP, TIFF |
| Documents | PDF, DOCX, DOC |
| Spreadsheets | XLSX, XLS, CSV |
| Code/text | PY, JS, TS, JAVA, GO, SQL, JSON, YAML, HTML, CSS, SH, TXT, MD, LOG, and more |

Large extracted text is truncated before being sent to the model.

## Notes

- This service is available only from the allowed office network range.
- There is no per-user login in the app.
- Chat history is saved on the server.
- Do not upload confidential data unless your team has approved using this local server for that data.
- For best results with images, use a vision-capable model.
