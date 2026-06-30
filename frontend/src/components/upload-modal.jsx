/** @jsx React.createElement */

const { useState, useRef } = React;

const { Icon, Modal, Spinner } = window.FTTH_APP;

function UploadModal({ onClose, onUpload }) {
  const [files, setFiles] = useState([]);
  const [customerId, setCustomerId] = useState('demo_customer');
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  const folderInputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files || []);
    if (dropped.length) { setFiles(dropped); setError(null); }
  }

  async function handleSubmit() {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    try {
      await onUpload(files, customerId);
    } catch (err) {
      setError(err.message || 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  }

  return (
    <Modal
      open
      title="Upload files"
      onClose={uploading ? () => {} : onClose}
      size="md"
      footer={(
        <>
          <button type="button" className="app-button app-btn-secondary" onClick={onClose} disabled={uploading}>Cancel</button>
          <button type="button" className="app-button app-button-primary text-white" onClick={handleSubmit} disabled={!files.length || uploading}>
            {uploading ? <span className="inline-flex items-center gap-2"><Spinner size={14} /> Uploading…</span> : `Upload ${files.length > 1 ? files.length + ' files' : 'file'}`}
          </button>
        </>
      )}
    >
      <div
        className={`upload-dropzone ${dragOver ? 'drag-over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
        aria-label="Drop files or click to browse"
      >
        {files.length ? (
          <div className="space-y-1">
            <p className="font-medium">{files.length} file{files.length > 1 ? 's' : ''} selected</p>
            <p className="text-xs text-[var(--app-text-soft)] truncate max-w-sm mx-auto">{files.map(f => f.name).join(', ')}</p>
          </div>
        ) : (
          <>
            <p className="mb-2 flex justify-center text-[var(--app-text-soft)]"><Icon name="upload" size={32} /></p>
            <p className="font-medium">Drop CSV, Excel, KML, KMZ, or ZIP here</p>
            <p className="text-sm text-[var(--app-text-soft)] mt-1">Or click to browse · CSV + KMZ together merges addresses with geometry</p>
          </>
        )}
        <input ref={inputRef} type="file" multiple accept=".csv,.xlsx,.xls,.kml,.kmz,.zip" className="hidden"
          onChange={(e) => { setFiles(Array.from(e.target.files || [])); setError(null); }} />
        <input ref={folderInputRef} type="file" multiple webkitdirectory="true" directory="true" className="hidden"
          onChange={(e) => { setFiles(Array.from(e.target.files || [])); setError(null); }} />
      </div>
      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
        <button type="button" onClick={() => inputRef.current?.click()} className="app-button app-btn-secondary text-sm">Select files</button>
        <button type="button" onClick={() => folderInputRef.current?.click()} className="app-button app-btn-secondary text-sm">Select folder</button>
      </div>
      <div className="mt-4 login-field">
        <label htmlFor="upload-customer">Customer ID</label>
        <input id="upload-customer" type="text" value={customerId} onChange={(e) => setCustomerId(e.target.value)}
          className="app-input w-full rounded-lg px-3 py-2 text-sm focus:outline-none" />
      </div>
      {error && (
        <div className="mt-3 p-3 rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 text-sm" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.UploadModal = UploadModal;
