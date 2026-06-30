/** @jsx React.createElement */

const { useState, useEffect, useCallback, useMemo } = React;

const {
  useRouter, ThemeContext, Layout, ProjectsPage, ProjectDetailPage, MapPage, LoginPage, OllamaChatPage,
  getToken, clearToken, getUser,
  ToastProvider, ConfirmProvider, ErrorBoundary,
} = window.FTTH_APP;

function AppRoutes({ user, setUser, path, navigate, themeValue }) {
  let content;
  if (!user) {
    content = <LoginPage onLogin={u => setUser(u)} />;
  } else if (path === '/chat') {
    content = <Layout user={user} onLogout={() => { clearToken(); setUser(null); }} lockViewport><OllamaChatPage /></Layout>;
  } else if (path.match(/^\/projects\/([^/]+)\/map$/)) {
    const jobId = path.match(/^\/projects\/([^/]+)\/map$/)[1];
    content = <Layout user={user} onLogout={() => { clearToken(); setUser(null); }} lockViewport><MapPage jobId={jobId} navigate={navigate} /></Layout>;
  } else if (path.match(/^\/projects\/([^/]+)$/)) {
    const jobId = path.match(/^\/projects\/([^/]+)$/)[1];
    content = <Layout user={user} onLogout={() => { clearToken(); setUser(null); }} lockViewport><ProjectDetailPage jobId={jobId} navigate={navigate} /></Layout>;
  } else {
    content = <Layout user={user} onLogout={() => { clearToken(); setUser(null); }} lockViewport><ProjectsPage navigate={navigate} /></Layout>;
  }

  return <ThemeContext.Provider value={themeValue}>{content}</ThemeContext.Provider>;
}

function App() {
  const [user, setUser] = useState(getToken() ? getUser() : null);
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('ftth_theme') === 'light' ? 'light' : 'dark';
    } catch (e) {
      return 'dark';
    }
  });
  const { path, navigate } = useRouter();

  const toggleTheme = useCallback(() => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.classList.toggle('viewport-locked', !!user);
    try {
      localStorage.setItem('ftth_theme', theme);
    } catch (e) {}
    const meta = document.querySelector('meta[name="theme-color"]:not([media])');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0f1117' : '#f3f6fb');
  }, [theme, user]);

  const themeValue = useMemo(() => ({
    theme,
    isDark: theme === 'dark',
    toggleTheme,
    setTheme,
  }), [theme, toggleTheme]);

  useEffect(() => {
    const handler = () => { clearToken(); setUser(null); };
    window.addEventListener('ftth-unauthorized', handler);
    return () => window.removeEventListener('ftth-unauthorized', handler);
  }, []);

  return (
    <ToastProvider>
      <ConfirmProvider>
        <ErrorBoundary>
          <AppRoutes user={user} setUser={setUser} path={path} navigate={navigate} themeValue={themeValue} />
        </ErrorBoundary>
      </ConfirmProvider>
    </ToastProvider>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.App = App;
