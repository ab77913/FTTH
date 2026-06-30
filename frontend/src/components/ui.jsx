/** @jsx React.createElement */
/**
 * Shared React UI primitives (Babel/JSX — requires React global).
 */
const { useState, useEffect } = React;

function AnimatedAppBackground() {
  return (
    <div className="app-bg-ambient" aria-hidden="true">
      <div className="app-bg-aurora"></div>
      <div className="app-bg-grid"></div>
    </div>
  );
}

function Icon({ name, size = 16, className = '' }) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    className,
    'aria-hidden': 'true',
  };
  const icons = {
    search: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></>,
    upload: <><path d="M12 3v12" /><path d="m7 8 5-5 5 5" /><path d="M5 21h14" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    table: <><path d="M3 6h18" /><path d="M3 12h18" /><path d="M3 18h18" /></>,
    map: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z" /><path d="M9 3v15" /><path d="M15 6v15" /></>,
    play: <polygon points="6 4 20 12 6 20 6 4" />,
    refresh: <><path d="M21 12a9 9 0 0 1-15.5 6.2" /><path d="M3 12A9 9 0 0 1 18.5 5.8" /><path d="M3 18h5v-5" /><path d="M21 6h-5v5" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    trash: <><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="m19 6-1 14H6L5 6" /><path d="M10 11v6" /><path d="M14 11v6" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a14 14 0 0 1 0 18" /><path d="M12 3a14 14 0 0 0 0 18" /></>,
    pin: <><path d="M12 21s7-5.4 7-11a7 7 0 0 0-14 0c0 5.6 7 11 7 11z" /><circle cx="12" cy="10" r="2.5" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3" /><path d="M12 19v3" /><path d="M4.93 4.93l2.12 2.12" /><path d="M16.95 16.95l2.12 2.12" /><path d="M2 12h3" /><path d="M19 12h3" /><path d="M4.93 19.07l2.12-2.12" /><path d="M16.95 7.05l2.12-2.12" /></>,
    download: <><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></>,
    sheet: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h8" /></>,
    bolt: <path d="m13 2-8 12h6l-1 8 8-12h-6z" />,
    expand: <><path d="M8 3H3v5" /><path d="M16 3h5v5" /><path d="M21 16v5h-5" /><path d="M3 16v5h5" /></>,
    collapse: <><path d="M8 3v5H3" /><path d="M16 3v5h5" /><path d="M21 16h-5v5" /><path d="M3 16h5v5" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="M4.9 4.9 6.3 6.3" /><path d="m17.7 17.7 1.4 1.4" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m4.9 19.1 1.4-1.4" /><path d="m17.7 6.3 1.4-1.4" /></>,
    moon: <path d="M21 12.8A8.5 8.5 0 1 1 11.2 3 6.5 6.5 0 0 0 21 12.8z" />,
    bot: <><rect x="5" y="8" width="14" height="10" rx="3" /><path d="M12 8V4" /><path d="M9 13h.01" /><path d="M15 13h.01" /><path d="M9 18v2h6v-2" /></>,
    check: <path d="m20 6-11 11-5-5" />,
    x: <><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>,
    circle: <circle cx="12" cy="12" r="8" />,
    arrowLeft: <><path d="M19 12H5" /><path d="m12 19-7-7 7-7" /></>,
    chevronUp: <path d="m18 15-6-6-6 6" />,
    chevronDown: <path d="m6 9 6 6 6-6" />,
    layers: <><path d="M12 2 2 7l10 5 10-5-10-5z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" /></>,
    externalLink: <><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>,
    ruler: <><path d="M2 12h20" /><path d="M6 8v8" /><path d="M10 10v4" /><path d="M14 8v8" /><path d="M18 10v4" /></>,
  };
  return <svg {...common}>{icons[name] || icons.circle}</svg>;
}

function useRouter() {
  const [path, setPath] = useState(window.location.hash.slice(1) || '/');
  useEffect(() => {
    const handler = () => setPath(window.location.hash.slice(1) || '/');
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  const navigate = (to) => { window.location.hash = to; };
  return { path, navigate };
}

function ProgressBar({ progress = 0, isActive = false, label, compact = false }) {
  const height = compact ? 'h-1.5' : 'h-2';
  return (
    <div>
      <div className={`w-full bg-slate-200 dark:bg-dark-600 rounded-full ${height} overflow-hidden`}>
        <div
          className={`${height} rounded-full transition-all duration-500 ease-out ${
            isActive ? 'bg-blue-500 progress-active' : progress >= 100 ? 'bg-green-500' : 'bg-blue-500'
          }`}
          style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
        />
      </div>
      {label && !compact && (
        <div className="flex items-center justify-between mt-1">
          <span className="text-xs text-slate-500 dark:text-slate-400">{progress}%</span>
          <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>
        </div>
      )}
    </div>
  );
}

window.FTTH_UI = {
  AnimatedAppBackground: AnimatedAppBackground,
  Icon: Icon,
  ProgressBar: ProgressBar,
  useRouter: useRouter,
};
