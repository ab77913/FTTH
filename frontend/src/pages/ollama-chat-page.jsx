/** @jsx React.createElement */

const { useState } = React;

const { API, getToken } = window.FTTH_APP;

function OllamaChatPage() {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const theme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  const token = getToken();
  const iframeSrc = `${API}/ollama/ui?_t=${encodeURIComponent(token || '')}&theme=${theme}`;

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="mb-3">
        <h1 className="page-title text-xl sm:text-2xl">Ollama Chat</h1>
        <p className="text-sm text-[var(--app-text-soft)]">Local vision and text assistant for FTTH workflows</p>
      </div>
      <div className="app-card rounded-xl flex-1 min-h-0 overflow-hidden relative">
        {!loaded && !failed && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[var(--app-surface)]/80 z-10">
            <span className="inline-block w-8 h-8 rounded-full border-2 border-cyan-500/30 border-t-cyan-500 animate-spin" role="status" aria-label="Loading chat" />
            <p className="text-sm text-[var(--app-text-soft)]">Connecting to Ollama…</p>
          </div>
        )}
        {failed && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-red-500 font-medium">Chat service unavailable</p>
            <p className="text-sm text-[var(--app-text-soft)]">Start Ollama locally or check tools/ollama setup.</p>
          </div>
        )}
        <iframe
          title="Ollama Chat"
          src={iframeSrc}
          className="w-full h-full min-h-[calc(100vh-8rem)] border-0"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.OllamaChatPage = OllamaChatPage;
