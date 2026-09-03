// Backend API base URL. Empty by default so relative paths keep working exactly as before in
// local dev (Vite's server.proxy in vite.config.js forwards /api and /static to
// http://localhost:8000). In production (frontend and backend on different domains, e.g.
// Vercel + Render) set VITE_API_BASE_URL to the deployed backend's URL at build time.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const CLIENT_ID_STORAGE_KEY = 'qa_client_id';

// Anonymous browser/client isolation (not authentication): a UUID generated once per browser
// and persisted in localStorage, so the backend can tell which scans belong to this browser
// apart from every other visitor's. Reused everywhere instead of regenerated - do not call
// crypto.randomUUID() anywhere else in the app.
export const getClientId = () => {
  let id = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, id);
  }
  return id;
};

// fetch() wrapper for backend API calls - use for every /api/... request instead of calling
// fetch() directly, so requests keep resolving to the backend once the two are on separate
// domains, and so every request is scoped to this browser's client ID.
export const apiFetch = (path, options = {}) => fetch(`${API_BASE_URL}${path}`, {
  ...options,
  headers: {
    ...(options.headers || {}),
    'X-Client-ID': getClientId(),
  },
});

// Resolves the Excel download URL for a scan. This is a plain browser navigation
// (window.open / <a href>), which cannot set custom headers, so the client ID travels as a
// query param here instead of the X-Client-ID header apiFetch uses - same trust level either
// way, since neither is a server-issued secret.
export const getScanDownloadUrl = (scanId) => `${API_BASE_URL}/api/scan/${scanId}/download?client_id=${getClientId()}`;

// Resolves a backend-relative path (e.g. a screenshot or download URL returned in an API
// response, like /static/screenshots/x.png) into an absolute URL. Leaves already-absolute
// URLs untouched.
export const resolveAssetUrl = (path) => {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path}`;
};
