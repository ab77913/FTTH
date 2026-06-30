/** @jsx React.createElement */

const { useState } = React;

const { Icon, useTheme, setToken, apiLogin, apiCreateAccount, apiForgotPassword, apiResetPassword, Spinner } = window.FTTH_APP;

function LoginPage({ onLogin }) {
  const { isDark, toggleTheme } = useTheme();
  const [mode, setMode] = useState('signin');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function completeLogin(data) {
    setToken(data.token);
    sessionStorage.setItem('ftth_user', data.username);
    onLogin(data.username);
  }

  function switchMode(nextMode) {
    setMode(nextMode);
    setError('');
    setMessage('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setMessage('');
    setLoading(true);
    try {
      if (mode === 'signin') {
        completeLogin(await apiLogin(username, password));
      } else if (mode === 'create') {
        if (password.length < 8) throw new Error('Password must be at least 8 characters');
        completeLogin(await apiCreateAccount({ username, password, display_name: displayName, email }));
      } else if (mode === 'forgot') {
        const data = await apiForgotPassword(username);
        setResetToken(data.reset_token || '');
        setMessage(`Reset code generated. Expires at ${data.expires_at}.`);
        setMode('reset');
      } else {
        if (newPassword.length < 8) throw new Error('Password must be at least 8 characters');
        completeLogin(await apiResetPassword(username, resetToken, newPassword));
      }
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  const title = mode === 'create' ? 'Create your account' : mode === 'forgot' ? 'Recover password' : mode === 'reset' ? 'Set new password' : 'Sign in to Meridian';
  const buttonText = mode === 'create' ? 'Create account' : mode === 'forgot' ? 'Send reset code' : mode === 'reset' ? 'Reset password' : 'Sign in';

  return (
    <div className="login-page min-h-screen relative flex items-center justify-center lg:justify-end px-4 sm:px-10 lg:px-16 py-8 overflow-y-auto">
      <div className="login-page-overlay absolute inset-0" aria-hidden="true" />
      <button type="button" onClick={toggleTheme}
        className="absolute right-5 top-5 z-20 w-10 h-10 rounded-xl border border-white/60 dark:border-dark-600 bg-white/80 dark:bg-dark-800/80 flex items-center justify-center shadow-lg backdrop-blur focus-visible:ring-2 focus-visible:ring-cyan-400"
        title="Toggle theme" aria-label="Toggle theme">
        <Icon name={isDark ? 'sun' : 'moon'} size={18} />
      </button>
      <div className="w-full max-w-md relative z-10 mr-0 lg:mr-[6%] xl:mr-[10%] my-auto">
        <div className="flex items-center justify-center gap-3 mb-6 sm:mb-8">
          <div className="w-11 h-11 bg-gradient-to-br from-cyan-500 to-blue-700 rounded-xl flex items-center justify-center font-bold text-lg text-white shadow-lg shadow-cyan-500/30">M</div>
          <div>
            <div className="font-bold text-xl">Meridian</div>
            <div className="text-xs text-[var(--app-text-soft)]">FTTH Data Ingestion Platform</div>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="app-card rounded-xl p-5 sm:p-8 shadow-2xl space-y-5" noValidate>
          <div className="grid grid-cols-3 rounded-lg bg-[var(--app-surface-soft)] p-1 text-xs font-semibold" role="tablist">
            {[
              ['signin', 'Sign in'],
              ['create', 'Create'],
              ['forgot', 'Forgot'],
            ].map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={mode === id || (id === 'forgot' && mode === 'reset')}
                onClick={() => switchMode(id)}
                className={`rounded-md py-2 transition ${(mode === id || (id === 'forgot' && mode === 'reset')) ? 'bg-[var(--app-surface)] shadow text-cyan-700 dark:text-cyan-300' : 'text-[var(--app-text-soft)]'}`}>
                {label}
              </button>
            ))}
          </div>
          <h2 className="text-lg font-semibold text-center">{title}</h2>
          {error && <div className="rounded-lg px-4 py-2.5 text-sm border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300" role="alert">{error}</div>}
          {message && <div className="rounded-lg px-4 py-2.5 text-sm border border-emerald-300 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300" role="status">{message}</div>}
          <div className="login-field">
            <label htmlFor="login-username">Username</label>
            <input id="login-username" type="text" value={username} onChange={e => setUsername(e.target.value)}
              required autoFocus autoComplete="username" className="app-input w-full rounded-lg px-4 py-2.5 text-sm focus:outline-none" placeholder="Enter username" />
          </div>
          {mode === 'create' && (
            <>
              <div className="login-field">
                <label htmlFor="login-display">Display name</label>
                <input id="login-display" type="text" value={displayName} onChange={e => setDisplayName(e.target.value)}
                  className="app-input w-full rounded-lg px-4 py-2.5 text-sm focus:outline-none" placeholder="Name shown in the app" />
              </div>
              <div className="login-field">
                <label htmlFor="login-email">Email</label>
                <input id="login-email" type="email" value={email} onChange={e => setEmail(e.target.value)}
                  className="app-input w-full rounded-lg px-4 py-2.5 text-sm focus:outline-none" placeholder="Optional" />
              </div>
            </>
          )}
          {(mode === 'signin' || mode === 'create') && (
            <div className="login-field">
              <label htmlFor="login-password">Password</label>
              <div className="login-password-wrap">
                <input id="login-password" type={showPassword ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)}
                  required autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  className="app-input w-full rounded-lg px-4 py-2.5 pr-16 text-sm focus:outline-none"
                  placeholder={mode === 'create' ? 'Min. 8 characters' : 'Enter password'} />
                <button type="button" className="login-password-toggle" onClick={() => setShowPassword(v => !v)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                  {showPassword ? 'HIDE' : 'SHOW'}
                </button>
              </div>
            </div>
          )}
          {mode === 'reset' && (
            <>
              <div className="login-field">
                <label htmlFor="login-reset-code">Reset code</label>
                <input id="login-reset-code" type="text" value={resetToken} onChange={e => setResetToken(e.target.value)} required
                  className="app-input w-full rounded-lg px-4 py-2.5 text-sm focus:outline-none" placeholder="Paste reset code" />
              </div>
              <div className="login-field">
                <label htmlFor="login-new-password">New password</label>
                <input id="login-new-password" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} required autoComplete="new-password"
                  className="app-input w-full rounded-lg px-4 py-2.5 text-sm focus:outline-none" placeholder="Min. 8 characters" />
              </div>
            </>
          )}
          <button type="submit" disabled={loading}
            className="app-button app-button-primary w-full text-white font-semibold rounded-lg py-2.5 text-sm flex items-center justify-center gap-2 disabled:opacity-60">
            {loading ? <><Spinner size={16} /> Working…</> : buttonText}
          </button>
          {mode === 'reset' && (
            <button type="button" onClick={() => switchMode('forgot')} className="w-full text-xs text-[var(--app-text-soft)] hover:text-cyan-600 dark:hover:text-cyan-400">
              Generate a new reset code
            </button>
          )}
        </form>
        <p className="text-center text-xs text-[var(--app-text-soft)] mt-6">Secure FTTH address validation · Agents 0–7</p>
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.LoginPage = LoginPage;
