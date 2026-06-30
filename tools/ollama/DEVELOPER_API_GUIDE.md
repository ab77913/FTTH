# FTTH Local Ollama Developer API Guide

This FTTH server can act as the company host machine for local Ollama models.
Teammates can use it through:

- FTTH web app AI chatbot
- Authenticated REST APIs for development
- Text, code, document, spreadsheet, and image/vision prompts
- Account-based chat history

The Ollama APIs are launched inside the same FTTH FastAPI server. No separate
Ollama chatbot port is required for teammates.

## Host Machine Setup

On the host machine, start Ollama and the FTTH application.

```powershell
ollama serve
```

In another terminal:

```powershell
start_app.bat
```

The FTTH API will proxy requests to local Ollama at:

```text
http://127.0.0.1:11434
```

Pull the models your team needs:

```powershell
ollama pull llama3.1:latest
ollama pull qwen2.5vl:latest
ollama pull qwen2.5-coder:latest
```

## Access

Use the FTTH app URL for the host machine.

Local host example:

```text
http://127.0.0.1:8000/
```

Office network example:

```text
http://HOST_MACHINE_IP:8000/
```

API base URL:

```text
http://HOST_MACHINE_IP:8000/api
```

Ollama API base URL:

```text
http://HOST_MACHINE_IP:8000/api/ollama
```

Ollama v1-compatible API base URL:

```text
http://HOST_MACHINE_IP:8000/api/ollama/v1
```

Replace `HOST_MACHINE_IP` with the LAN IP address of the host machine.

## Login And Tokens

Every API call requires a FTTH login token.

Default existing accounts:

```text
admin / Meridian@2026
ftth_team / Meridian@2026
```

Login:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"Meridian@2026\"}"
```

Example response:

```json
{
  "token": "JWT_TOKEN_HERE",
  "username": "admin",
  "display_name": "Admin"
}
```

Use this token in later requests:

```http
Authorization: Bearer JWT_TOKEN_HERE
```

For browser downloads or embedded UI, the token can also be passed as:

```text
?_t=JWT_TOKEN_HERE
```

## Account APIs

Create account:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/accounts \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"developer1\",\"password\":\"Password123\",\"display_name\":\"Developer One\",\"email\":\"dev@example.com\"}"
```

Generate password reset code:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/accounts/forgot-password \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"developer1\"}"
```

This returns a reset code directly because no email service is configured.

Reset password:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/accounts/reset-password \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"developer1\",\"reset_token\":\"RESET_CODE\",\"new_password\":\"NewPassword123\"}"
```

## Use The Chatbot UI

1. Open the FTTH app.
2. Sign in.
3. Click the `AI` button in the left sidebar.
4. Choose a model from the model dropdown.
5. Type a message and press Enter.
6. Attach images, PDFs, Word files, spreadsheets, code files, or text files if needed.

Direct embedded chatbot URL:

```text
http://HOST_MACHINE_IP:8000/api/ollama/ui?_t=JWT_TOKEN_HERE&theme=dark
```

For light theme:

```text
http://HOST_MACHINE_IP:8000/api/ollama/ui?_t=JWT_TOKEN_HERE&theme=light
```

## API Quick Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/login` | Sign in and get JWT token |
| POST | `/api/accounts` | Create a user account |
| POST | `/api/accounts/forgot-password` | Generate password reset code |
| POST | `/api/accounts/reset-password` | Reset password with code |
| GET | `/api/ollama/health` | Check FTTH/Ollama status |
| GET | `/api/ollama/models` | List available Ollama models |
| GET | `/api/ollama/ui` | Embedded chatbot UI |
| POST | `/api/ollama/public/extract-text` | No-login text extraction from uploaded files |
| POST | `/api/ollama/chat` | Multipart chat for text/file prompts |
| POST | `/api/ollama/v1/chat` | JSON or multipart development chat API |
| GET | `/api/ollama/conversations` | List current account conversations |
| POST | `/api/ollama/conversations` | Create conversation |
| GET | `/api/ollama/conversations/{id}` | Get one conversation |
| PATCH | `/api/ollama/conversations/{id}` | Rename/change model |
| DELETE | `/api/ollama/conversations/{id}` | Delete conversation |
| GET | `/api/ollama/uploads/{filename}` | Download/view chat attachment |

All `/api/ollama/v1/...` endpoints are also available for developers who prefer
the versioned path.

## Health Check

```bash
curl http://HOST_MACHINE_IP:8000/api/ollama/health \
  -H "Authorization: Bearer JWT_TOKEN_HERE"
```

Example response:

```json
{
  "status": "ok",
  "ollama": "reachable",
  "models": 3
}
```

If Ollama is not running, this endpoint returns a degraded status.

## List Available Models

```bash
curl http://HOST_MACHINE_IP:8000/api/ollama/models \
  -H "Authorization: Bearer JWT_TOKEN_HERE"
```

Use the exact model name returned by this endpoint in chat requests.

Recommended examples:

| Use Case | Model Example |
|----------|---------------|
| General chat | `llama3.1:latest` |
| Vision/image analysis | `qwen2.5vl:latest` |
| House number OCR | `qwen2.5vl:latest` |
| Code generation/review | `qwen2.5-coder:latest` |

Available model names can change depending on what is installed on the host.

## Text Chat API

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.1:latest\",\"message\":\"Explain REST APIs in simple terms.\",\"stream\":false}"
```

Example response:

```json
{
  "success": true,
  "response": "REST APIs let applications communicate over HTTP...",
  "model": "llama3.1:latest",
  "conversation_id": 42
}
```

## Continue A Conversation

The first response includes `conversation_id`. Reuse it to keep context.

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.1:latest\",\"message\":\"My project uses FastAPI.\",\"stream\":false}"
```

Then:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.1:latest\",\"message\":\"What framework did I say I use?\",\"conversation_id\":42,\"stream\":false}"
```

## Streaming Responses

Set `stream` to `true`. The response is newline-delimited JSON.

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.1:latest\",\"message\":\"Write a short summary about local LLMs.\",\"stream\":true}"
```

The first line contains metadata:

```json
{"type":"meta","conversation_id":42}
```

Later lines contain Ollama response chunks.

## Vision LLM / Image API

Use multipart form data and attach the image as `file`.

When an image is uploaded, the FTTH server first creates an enhanced copy for
the LLM. The enhancement step corrects orientation, upscales small images,
improves contrast, sharpens edges, and reduces blur as much as possible before
encoding the image for Ollama. The original upload is still kept in chat history.

No-login text extraction endpoint:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/public/extract-text \
  -F "model=qwen2.5vl:latest" \
  -F "message=Extract all visible text and numbers from this image." \
  -F "stream=false" \
  -F "file=@photo.jpg"
```

Authenticated chat endpoint:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -F "model=qwen2.5vl:latest" \
  -F "message=Describe this image and extract visible numbers." \
  -F "stream=false" \
  -F "file=@photo.jpg"
```

Supported image formats include:

```text
PNG, JPG, JPEG, GIF, WebP, BMP, TIFF
```

## Document Analysis API

PDF:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -F "model=llama3.1:latest" \
  -F "message=Summarize this document and list action items." \
  -F "stream=false" \
  -F "file=@report.pdf"
```

Word document:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -F "model=llama3.1:latest" \
  -F "message=Extract the important points from this document." \
  -F "stream=false" \
  -F "file=@notes.docx"
```

Spreadsheet:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -F "model=llama3.1:latest" \
  -F "message=Analyze this data and highlight trends." \
  -F "stream=false" \
  -F "file=@data.xlsx"
```

CSV is treated as text and can also be uploaded.

## Code Review API

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/v1/chat \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -F "model=qwen2.5-coder:latest" \
  -F "message=Review this code for bugs, security issues, and performance improvements." \
  -F "stream=false" \
  -F "file=@app.py"
```

## Conversation APIs

List conversations for the logged-in account:

```bash
curl http://HOST_MACHINE_IP:8000/api/ollama/conversations \
  -H "Authorization: Bearer JWT_TOKEN_HERE"
```

Create conversation:

```bash
curl -X POST http://HOST_MACHINE_IP:8000/api/ollama/conversations \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Address validation helper\",\"model\":\"llama3.1:latest\"}"
```

Get one conversation:

```bash
curl http://HOST_MACHINE_IP:8000/api/ollama/conversations/42 \
  -H "Authorization: Bearer JWT_TOKEN_HERE"
```

Rename a conversation:

```bash
curl -X PATCH http://HOST_MACHINE_IP:8000/api/ollama/conversations/42 \
  -H "Authorization: Bearer JWT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"New title\"}"
```

Delete a conversation:

```bash
curl -X DELETE http://HOST_MACHINE_IP:8000/api/ollama/conversations/42 \
  -H "Authorization: Bearer JWT_TOKEN_HERE"
```

## Python Examples

Install requests if needed:

```bash
pip install requests
```

Login and text chat:

```python
import requests

HOST = "http://HOST_MACHINE_IP:8000"

login = requests.post(f"{HOST}/api/login", json={
    "username": "developer1",
    "password": "Password123",
})
login.raise_for_status()
token = login.json()["token"]

headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    f"{HOST}/api/ollama/v1/chat",
    headers=headers,
    json={
        "model": "llama3.1:latest",
        "message": "Explain APIs in two paragraphs.",
        "stream": False,
    },
)
response.raise_for_status()
data = response.json()

print(data["response"])
print("Conversation ID:", data["conversation_id"])
```

Image analysis:

```python
import requests

HOST = "http://HOST_MACHINE_IP:8000"

with open("photo.jpg", "rb") as image:
    response = requests.post(
        f"{HOST}/api/ollama/public/extract-text",
        data={
            "model": "qwen2.5vl:latest",
            "message": "Extract all visible text from this image.",
            "stream": "false",
        },
        files={"file": image},
    )

response.raise_for_status()
print(response.json()["response"])
```

Continue a conversation:

```python
import requests

HOST = "http://HOST_MACHINE_IP:8000"
TOKEN = "JWT_TOKEN_HERE"
headers = {"Authorization": f"Bearer {TOKEN}"}

conversation_id = None

for message in ["My project uses FastAPI.", "What framework did I say I use?"]:
    payload = {
        "model": "llama3.1:latest",
        "message": message,
        "stream": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    response = requests.post(f"{HOST}/api/ollama/v1/chat", headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    conversation_id = data["conversation_id"]
    print(data["response"])
```

## JavaScript Examples

Login and text chat:

```javascript
const HOST = "http://HOST_MACHINE_IP:8000";

async function login(username, password) {
  const response = await fetch(`${HOST}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function chat() {
  const auth = await login("developer1", "Password123");

  const response = await fetch(`${HOST}/api/ollama/v1/chat`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${auth.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "llama3.1:latest",
      message: "Give me three ideas for automating reports.",
      stream: false,
    }),
  });

  const data = await response.json();
  console.log(data.response);
}

chat();
```

Image upload from browser:

```javascript
const HOST = "http://HOST_MACHINE_IP:8000";

async function analyzeImage(file) {
  const form = new FormData();
  form.append("model", "qwen2.5vl:latest");
  form.append("message", "Extract all visible text from this image.");
  form.append("stream", "false");
  form.append("file", file);

  const response = await fetch(`${HOST}/api/ollama/public/extract-text`, {
    method: "POST",
    body: form,
  });

  const data = await response.json();
  console.log(data.response);
}
```

## Supported File Types

| Type | Formats |
|------|---------|
| Images | PNG, JPG, JPEG, GIF, WebP, BMP, TIFF |
| Documents | PDF, DOCX, DOC |
| Spreadsheets | XLSX, XLS, CSV |
| Code/text | PY, JS, TS, JAVA, GO, SQL, JSON, YAML, HTML, CSS, SH, TXT, MD, LOG, and more |

Large extracted text is truncated before being sent to the model.

## How Account Isolation Works

- Each teammate has a FTTH account.
- Each API request uses that account's JWT token.
- Chat conversations are stored with the account owner.
- One account cannot list or open another account's conversations.
- FTTH app data-processing APIs still use the same app permissions model.

## Host Machine Notes

- Keep Ollama running on the host machine.
- Keep the FTTH FastAPI app running on the host machine.
- Open Windows Firewall for the FTTH API port if teammates need LAN access.
- Share only the FTTH host URL, not the raw Ollama port.
- Developers should call `/api/ollama/...`, not `http://127.0.0.1:11434`, unless they are developing directly on the host.

## Troubleshooting

If login fails:

- Confirm the account exists.
- Reset the password from the login page.
- Existing default login is `admin / Meridian@2026`.

If `/api/ollama/health` says degraded:

- Check that Ollama is running.
- Run `ollama list` on the host.
- Pull at least one model with `ollama pull`.

If a model is not found:

- Check `GET /api/ollama/models`.
- Use the exact model name from the response.
- Pull the model on the host machine.

If teammates cannot connect:

- Confirm they can reach `http://HOST_MACHINE_IP:8000/`.
- Check Windows Firewall.
- Confirm they are on the same office network or VPN.

## Security Notes

- Do not expose this service publicly without network restrictions.
- Do not share JWT tokens.
- Do not upload confidential data unless approved for this local host.
- Rotate passwords when someone leaves the project.
- The local reset-code flow displays the code in the UI because email delivery is not configured.
