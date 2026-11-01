import { createServer } from 'node:net';

/** Probe and release a loopback port; uvicorn owns the subsequent bind. */
export async function selectBackendPort(value = process.env.BACKEND_PORT) {
  const port = value === undefined ? 0 : Number(value);
  if (value !== undefined && !/^\d+$/.test(value)
    || !Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error('BACKEND_PORT must be an integer between 0 and 65535.');
  }
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error(`Unable to bind backend port ${port}.`)));
    server.listen({ host: '127.0.0.1', port, exclusive: true }, () => {
      const selected = server.address().port;
      server.close(error => error ? reject(error) : resolve(selected));
    });
  });
}
