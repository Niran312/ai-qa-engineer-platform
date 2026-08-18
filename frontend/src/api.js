// Backend API base URL. Empty by default so relative paths keep working exactly as before in
// local dev (Vite's server.proxy in vite.config.js forwards /api and /static to
// http://localhost:8000). In production (frontend and backend on different domains, e.g.
// Vercel + Render) set VITE_API_BASE_URL to the deployed backend's URL at build time.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

// fetch() wrapper for backend API calls - use for every /api/... request instead of calling
// fetch() directly, so requests keep resolving to the backend once the two are on separate
// domains.
export const apiFetch = (path, options) => fetch(`${API_BASE_URL}${path}`, options);

// Resolves a backend-relative path (e.g. a screenshot or download URL returned in an API
// response, like /static/screenshots/x.png) into an absolute URL. Leaves already-absolute
// URLs untouched.
export const resolveAssetUrl = (path) => {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path}`;
};
