# Amadeus Desktop

Tauri 2 + React 19 + TypeScript + Vite front end. Setup and background are in the [root README](../README.md).

- Development (default local mock): run `npm ci`, then `npm run dev -- --port 1420 --strictPort`, and open http://localhost:1420/?scenario=streaming. No backend, API key, model or TTS server is needed. Clear a previous `AMADEUS_DEV_MOCK=0` override first; see the [scenario guide](src/dev-mock/README.md).
- Real-backend development: start the backend on `127.0.0.1:8000`, set `AMADEUS_DEV_MOCK=0` before starting Vite, and use the same port 1420. Vite then proxies `/api`, `/ws` and `/health` to the backend (`AMADEUS_BACKEND_ORIGIN` overrides its origin).
- The mock is development-only and is excluded from production builds; `npm run preview` does not provide it.
- Checks: `npm run test` and `npm run build`.
- The Tauri shell in `src-tauri/` is set up for the Windows installer build, which bundles the backend through packaging scripts that are not in this repository, so `npm run tauri -- dev` does not start from a plain checkout.

## M3 worldline behavior

M3 is accepted and merged through [PR #3](https://github.com/meditecnics/AMADEUS/pull/3). Open **系统 → 世界线**: the menu instrument shows the current reading, the diagram endpoint previews the selected target, and the shift button starts the transaction. Header, menu and transition use the same cathode shapes. Click and Esc share the skip action: target readiness and the final digit lock still gate publication; failure retains the source worldline. Reduced-motion and CSS fallback keep that transaction.

The [MIT-vendored runtime](src/worldline-engine/README.vendor.md) prepares the GPU stage asynchronously during idle time after entry or while the menu is open, reuses a healthy stage and provides a bounded cold-start fallback. Softer entry, exit and skip handoffs keep the picture's brightness consistent across repeated shifts. No runtime dependencies were added.

Caption Scheme A leaves normal GPU playback visually silent while retaining screen-reader status. A wait lasting more than 0.7 seconds reveals an unboxed amber caption; failure status is exposed immediately in the error color. The CSS instrument keeps its caption. The Esc hint appears on pointer/keyboard activity, keyboard focus, and briefly during the first three shifts; this changes discoverability, not skip semantics.

Login warms the workstation's fonts in idle time, using a seed on first use. After the workstation settles, it records the loaded faces; refresh to let the next login preload that record. Existing audio initialization now runs on login interaction before entry, with no new audio features.

Prior validation and the developer's actual Edge acceptance are recorded in the [root engineering notes](../README.md#engineering-notes). This documentation closeout did not rerun builds or tests and makes no fixed-millisecond claim for native GPU startup.
