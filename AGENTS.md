# Working in AMADEUS

AMADEUS is a Windows-first desktop companion app. The frontend is in `desktop/`
(Tauri 2, React and TypeScript); the Python/FastAPI backend is in `backend/`.

## Install and check

Use Python 3.11 in an activated virtual environment and Node.js 22 (22.13+).
The commands below follow `.github/workflows/ci.yml`; `desktop/package.json`
defines the frontend scripts. Run backend commands from `backend/`:

```sh
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install ruff==0.17.2
python -m pytest -q -rfEsx
ruff check
```

Run frontend commands from `desktop/`:

```sh
npm ci
npx tsc
npm test -- --maxWorkers=2
npm run build
```

`npm run build` runs TypeScript checking followed by the Vite production build.
CI runs the frontend and backend suites on Windows. Windows-only tests skip on
Linux; Linux results do not establish Windows acceptance.

## Repository rules

- Let Git handle line endings. Text is stored as LF in Git; `.gitattributes`
  requests CRLF in the working tree. Do not bulk-convert line endings. The CI
  EOL check, run from a clean repository root, must produce no staged diff:

  ```sh
  git add --renormalize .
  git diff --cached --name-only --exit-code
  ```

- Merge with merge commits only. Do not squash or rebase merges:
  `.git-blame-ignore-revs` depends on preserving the normalization commit ID.
- Do not commit secrets or personal email addresses, including in commit
  metadata.
- Do not add audio or voice model weights. Do not use original game
  materials (character art, CG, logos, BGM, game screenshots or anything derived
  from them); those belong only in the separately distributed asset pack. See
  README sections on optional voice and the asset pack.
- First-party generated images (textures, illustrations) may be added. Do not
  generate characters from the original work, and do not name the original
  work, its characters or a living artist in prompts. Register every file in
  `assets/PROVENANCE.md` in the same commit: path, generator, date, full
  prompt and who generated it. Commit only final, cropped picks (WebP or PNG,
  at most 500 KB each): Git history is permanent.
- Before importing backend `app` outside pytest, set `AMADEUS_DATA_DIR` to a
  fresh temporary directory and leave `AMADEUS_DB_PATH` unset (it overrides the
  data directory). Paths are resolved during import. Source and frozen runs refuse
  to start without a nonempty `AMADEUS_DATA_DIR`; they have no fallback user
  directory. Never point development code at installed user data.
  `backend/tests/conftest.py` sets up isolation before pytest collection; keep
  that isolation intact.
- Unless the task explicitly requests it, preserve cross-module interfaces:
  WebSocket messages and close codes, audio interfaces, memory APIs, model
  service interfaces, `prompt_compiler`, ports and startup tokens.
