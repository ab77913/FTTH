# FTTH Frontend

React UI (Babel standalone, no build step) for the Meridian FTTH Data Ingestion app.

## Folder structure

```
frontend/
  public/
    index.html          # HTML shell — CDN scripts + ordered /assets/ loads
    images/             # Static images (login background, etc.)
  src/
    styles/
      app.css           # Global application styles
      map.css           # Map workspace, Leaflet, layers, and Street View styles
    api/
      theme.js          # Theme init (localStorage → dark/light class)
      http-client.js    # debounce, in-flight GET dedupe (window.FTTH_HTTP)
      auth.js           # Auth + apiFetch (window.FTTH base)
      data-api.js       # Jobs, records, uploads, settings APIs
    config/
      pipeline.js       # Pipeline defaults, flow builder helpers
    components/
      ui.jsx            # Icon, ProgressBar, useRouter, AnimatedAppBackground
      agent-progress-panel.jsx
      upload-modal.jsx
      records-table.jsx
      flow-builder-modal.jsx
      app-settings-modal.jsx
      layout.jsx        # App chrome (sidebar, theme toggle, settings)
      map-search-panel.jsx
      map-selected-panel.jsx
    pages/
      projects-page.jsx
      project-detail-page.jsx
      map-page.jsx
      login-page.jsx
      ollama-chat-page.jsx
    app/
      theme-context.jsx # ThemeContext + useTheme
      globals.jsx       # Merges window.FTTH + window.FTTH_UI onto window.FTTH_APP
      app.jsx           # Root App + hash routing
      bootstrap.jsx     # ReactDOM.createRoot mount
```

## Runtime globals

| Global | Purpose |
|--------|---------|
| `window.FTTH` | API client, auth, pipeline config helpers |
| `window.FTTH_HTTP` | HTTP utilities (debounce, dedupe) |
| `window.FTTH_UI` | Shared UI primitives from `ui.jsx` |
| `window.FTTH_APP` | React components, pages, theme context, shared helpers |

Each JSX module registers its exports on `window.FTTH_APP` (e.g. `FTTH_APP.LoginPage`).

## Serving

FastAPI should mount:

- `frontend/public/` at `/` (or serve `index.html` for SPA routes)
- `frontend/src/` at `/assets`

Script and stylesheet load order is defined in `public/index.html`. Do not reorder without checking cross-file dependencies. Map-specific CSS lives in `styles/map.css`, and map-page UI islands that can evolve independently live in `components/map-*.jsx`.
