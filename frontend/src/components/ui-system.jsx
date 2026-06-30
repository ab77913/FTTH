/** @jsx React.createElement */
/**
 * Production UI system: toasts, confirmations, modals, loading, empty states, error boundary.
 */
const { useState, useEffect, useCallback, useRef, createContext, useContext, Component } = React;

// ─── Toast ───────────────────────────────────────────────────────────────────

const ToastContext = createContext({ toast: () => {} });

function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const toast = useCallback((message, type = 'info', duration = 4500) => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev, { id, message, type }]);
    window.setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, duration);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite" aria-atomic="false">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`}>
            <span className="toast-dot" aria-hidden="true" />
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function useToast() {
  return useContext(ToastContext);
}

// ─── Confirm dialog ──────────────────────────────────────────────────────────

const ConfirmContext = createContext({ confirm: async () => false });

function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);
  const resolverRef = useRef(null);

  const confirm = useCallback((options) => {
    const opts = typeof options === 'string'
      ? { title: 'Confirm', message: options, confirmLabel: 'Confirm', variant: 'danger' }
      : options;
    return new Promise((resolve) => {
      resolverRef.current = resolve;
      setState(opts);
    });
  }, []);

  function close(result) {
    setState(null);
    if (resolverRef.current) {
      resolverRef.current(result);
      resolverRef.current = null;
    }
  }

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {state && (
        <Modal
          open
          title={state.title || 'Confirm'}
          onClose={() => close(false)}
          size="sm"
          footer={(
            <>
              <button type="button" className="app-button app-btn-secondary" onClick={() => close(false)}>
                {state.cancelLabel || 'Cancel'}
              </button>
              <button
                type="button"
                className={`app-button ${state.variant === 'danger' ? 'app-btn-danger' : 'app-button-primary text-white'}`}
                onClick={() => close(true)}
              >
                {state.confirmLabel || 'Confirm'}
              </button>
            </>
          )}
        >
          <p className="text-sm text-[var(--app-text-soft)] leading-relaxed whitespace-pre-line">{state.message}</p>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

function useConfirm() {
  return useContext(ConfirmContext);
}

// ─── Modal (accessible) ──────────────────────────────────────────────────────

function Modal({ open, title, children, onClose, footer, size = 'md' }) {
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    const t = window.setTimeout(() => panelRef.current?.focus(), 0);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
      window.clearTimeout(t);
    };
  }, [open, onClose]);

  if (!open) return null;

  const sizeClass = size === 'sm' ? 'max-w-md' : size === 'lg' ? 'max-w-2xl' : 'max-w-lg';

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        className={`modal-panel app-card ${sizeClass}`}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="modal-title" className="modal-title">{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            <span aria-hidden="true">&times;</span>
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ─── Loading & empty states ──────────────────────────────────────────────────

function Spinner({ size = 20, className = '' }) {
  return (
    <span
      className={`inline-block rounded-full border-2 border-cyan-500/30 border-t-cyan-500 animate-spin ${className}`}
      style={{ width: size, height: size }}
      role="status"
      aria-label="Loading"
    />
  );
}

function Skeleton({ className = '', style }) {
  return <div className={`skeleton ${className}`} style={style} aria-hidden="true" />;
}

function PageLoader({ message = 'Loading…' }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-4">
      <Spinner size={32} />
      <p className="text-sm text-[var(--app-text-soft)]">{message}</p>
    </div>
  );
}

function EmptyState({ icon, title, description, action }) {
  return (
    <div className="empty-state app-card rounded-xl">
      {icon && <div className="empty-state-icon">{icon}</div>}
      <h3 className="empty-state-title">{title}</h3>
      {description && <p className="empty-state-desc">{description}</p>}
      {action}
    </div>
  );
}

function StatusBadge({ status, pulse = false }) {
  const key = String(status || '').toUpperCase();
  const map = {
    COMPLETED: 'badge-success',
    PROCESSING: 'badge-warning',
    PARTIAL: 'badge-info',
    FAILED: 'badge-danger',
  };
  const cls = map[key] || 'badge-neutral';
  return (
    <span className={`status-badge ${cls} ${pulse ? 'badge-pulse' : ''}`}>
      {status}
    </span>
  );
}

function Breadcrumbs({ items }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumbs-list">
        {items.map((item, i) => (
          <li key={item.label + i} className="breadcrumbs-item">
            {i > 0 && <span className="breadcrumbs-sep" aria-hidden="true">/</span>}
            {item.href && i < items.length - 1 ? (
              <button type="button" className="breadcrumbs-link" onClick={item.onClick}>{item.label}</button>
            ) : (
              <span className="breadcrumbs-current" aria-current={i === items.length - 1 ? 'page' : undefined}>{item.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

function PageHeader({ eyebrow, title, description, actions }) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        {eyebrow && <p className="page-eyebrow">{eyebrow}</p>}
        <h1 className="page-title">{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}

function ErrorBoundary({ children, fallback }) {
  return (
    <ErrorBoundaryClass fallback={fallback}>{children}</ErrorBoundaryClass>
  );
}

class ErrorBoundaryClass extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback(this.state.error);
      return (
        <div className="app-card rounded-xl p-8 max-w-lg mx-auto mt-12 text-center">
          <div className="text-red-500 mb-3 text-lg font-semibold">Something went wrong</div>
          <p className="text-sm text-[var(--app-text-soft)] mb-4">{this.state.error.message || 'An unexpected error occurred.'}</p>
          <button type="button" className="app-button app-button-primary text-white px-4 py-2 rounded-lg" onClick={() => window.location.reload()}>
            Reload application
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

Object.assign(window.FTTH_UI || (window.FTTH_UI = {}), {
  ToastProvider,
  ConfirmProvider,
  useToast,
  useConfirm,
  Modal,
  Spinner,
  Skeleton,
  PageLoader,
  EmptyState,
  StatusBadge,
  Breadcrumbs,
  PageHeader,
  ErrorBoundary,
});
