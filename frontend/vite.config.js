import { defineConfig } from 'vite';

// NOTE ON VITE_API_BASE_URL / .env
// --------------------------------
// `src/lib/api.js` uses `import.meta.env.VITE_API_BASE_URL || ""`.
// In PRODUCTION the frontend is always served same-origin by the FastAPI app
// (Render, or OCI behind nginx), so the base URL MUST be "".
//
// `frontend/.env` is a local-only dev convenience (gitignored) and typically
// points at http://localhost:8001 for the ingest worker. If Vite loaded it
// during a production build, `npm run build` would bake "localhost:8001" into
// `lawfirm-rag/static/` — which Render serves verbatim (its buildCommand only
// runs pip), silently breaking the deployed frontend.
//
// So: production builds ignore .env entirely. Dev keeps using it.
export default defineConfig(({ mode }) => ({
  envDir: mode === 'production' ? false : undefined,
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/auth': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/lawfirm-chat-trigger-006': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
  },
}));
