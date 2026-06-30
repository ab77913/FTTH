/** @jsx React.createElement */

const { useState } = React;

const { AnimatedAppBackground, Icon, useRouter, useTheme, AppSettingsModal, OLLAMA_CHAT_URL } = window.FTTH_APP;

function SidebarButton({ active, title, onClick, children }) {
  return (
    <button type="button" onClick={onClick} className={`sidebar-nav-btn app-button ${active ? 'active' : ''}`} title={title}>
      {children}
      <span className="sidebar-tooltip">{title}</span>
    </button>
  );
}

function Layout({ children, user, onLogout, lockViewport = false }) {
  const { isDark, toggleTheme } = useTheme();
  const { path, navigate } = useRouter();
  const [showSettings, setShowSettings] = useState(false);
  const initials = (user || 'U').slice(0, 2).toUpperCase();
  const onProjects = path === '/' || path === '';
  const onChat = path === '/chat';

  return (
    <div className={`${lockViewport ? 'h-screen overflow-hidden' : 'min-h-screen overflow-x-hidden'} relative bg-transparent text-slate-900 dark:text-slate-100 transition-colors`}>
      <AnimatedAppBackground />
      <header className="fixed left-0 top-0 bottom-0 z-40 w-[var(--sidebar-width)] border-r border-[color:var(--app-border)] bg-[var(--app-surface)]/95 backdrop-blur-xl px-0 py-4 flex flex-col items-center justify-between gap-4 shadow-lg shadow-slate-900/5 dark:shadow-black/20">
        <div className="flex flex-col items-center gap-3">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-sm text-white bg-gradient-to-br from-cyan-500 to-blue-700 shadow-lg shadow-cyan-500/30 ring-1 ring-white/20 hover:scale-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 transition"
            title="Meridian home">
            M
            <span className="sr-only">Meridian homepage</span>
          </button>
          <nav className="flex flex-col items-center gap-2 mt-2" aria-label="Main">
            <SidebarButton active={onProjects} title="Projects" onClick={() => navigate('/')}>
              <Icon name="grid" size={18} />
            </SidebarButton>
            <SidebarButton active={onChat} title="Ollama Chat" onClick={() => navigate(OLLAMA_CHAT_URL)}>
              <Icon name="bot" size={18} />
            </SidebarButton>
          </nav>
        </div>
        <div className="flex flex-col items-center gap-2 flex-shrink-0">
          <SidebarButton active={false} title={isDark ? 'Light mode' : 'Dark mode'} onClick={toggleTheme}>
            <Icon name={isDark ? 'sun' : 'moon'} size={18} />
          </SidebarButton>
          <SidebarButton active={false} title="API settings" onClick={() => setShowSettings(true)}>
            <Icon name="settings" size={18} />
          </SidebarButton>
          <div className="flex flex-col items-center gap-2 pt-3 border-t border-[color:var(--app-border)] w-full px-2">
            <div className="w-9 h-9 bg-gradient-to-br from-blue-600 to-cyan-600 rounded-full flex items-center justify-center font-bold text-sm text-white shadow-lg" title={`${user} — signed in`}>{initials}</div>
            <SidebarButton active={false} title="Sign out" onClick={onLogout}>
              <Icon name="arrowLeft" size={15} />
            </SidebarButton>
          </div>
        </div>
      </header>
      <main className={lockViewport
        ? 'app-main relative box-border p-1.5 sm:p-2 pl-[calc(var(--sidebar-width)+8px)] sm:pl-[calc(var(--sidebar-width)+12px)] overflow-hidden'
        : 'relative p-3 sm:p-6 pl-[calc(var(--sidebar-width)+10px)] sm:pl-[calc(var(--sidebar-width)+16px)] max-w-[1680px] mx-auto min-h-screen overflow-x-hidden'}>
        {children}
      </main>
      {showSettings && <AppSettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.Layout = Layout;
