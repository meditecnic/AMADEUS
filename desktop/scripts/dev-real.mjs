import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const desktop = fileURLToPath(new URL('../', import.meta.url));
const backend = path.resolve(desktop, '../backend');
if (!process.env.AMADEUS_DATA_DIR?.trim()) {
  console.error('Set AMADEUS_DATA_DIR to a separate development directory before running.');
  process.exit(1);
}
const token = randomBytes(32).toString('hex');
const environment = { ...process.env };
delete environment.AMADEUS_DB_PATH;
// Give each process only its own server-side token variable.
delete environment.AMADEUS_LOCAL_API_TOKEN;
delete environment.AMADEUS_DEV_LOCAL_API_TOKEN;
const python = process.env.AMADEUS_PYTHON || path.join(backend, '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const children = [];
let stopping = false;
function stop(code) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) continue;
    if (process.platform === 'win32') {
      // Include any backend sidecars and Vite's build helper.
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      try { process.kill(-child.pid, 'SIGTERM'); } catch { /* Already exited. */ }
    }
  }
}
function launch(command, args, cwd, env) {
  const child = spawn(command, args, {
    cwd, env, stdio: 'inherit', detached: process.platform !== 'win32',
  });
  children.push(child);
  child.on('error', () => {
    console.error('Unable to start a development process; check AMADEUS_PYTHON and npm dependencies.');
    stop(1);
  });
  child.on('exit', code => stop(code ?? 1));
}
process.on('SIGINT', () => stop(130));
process.on('SIGTERM', () => stop(143));
launch(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'],
  backend, { ...environment, AMADEUS_LOCAL_API_TOKEN: token });
launch(process.execPath, [path.join(desktop, 'node_modules/vite/bin/vite.js'), ...process.argv.slice(2)],
  desktop, { ...environment, AMADEUS_DEV_MOCK: '0', AMADEUS_DEV_LOCAL_API_TOKEN: token });
