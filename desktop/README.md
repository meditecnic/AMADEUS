# Amadeus Desktop

Tauri 2 + React 19 + TypeScript + Vite front end. Setup and background are in the [root README](../README.md).

- Development: run the backend on `127.0.0.1:8000`, then `npm ci` and `npm run dev`, and open http://localhost:1420. Vite proxies `/api`, `/ws` and `/health` to the backend.
- Checks: `npm run test` and `npm run build`.
- The Tauri shell in `src-tauri/` is set up for the Windows installer build, which bundles the backend through packaging scripts that are not in this repository, so `npm run tauri -- dev` does not start from a plain checkout.
