// Loaded by the user's DeepSeek desktop profile. It publishes the original
// Host's normal launch URL to a private, same-device bridge file. No signing
// key is read, no cookie is forged, and no extra network listener is created.
import { writeFile, rename, unlink } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { spawn } from 'node:child_process';
import { openSync, closeSync } from 'node:fs';
export const name = 'agent-watch-connection';
export const inject = ['connection', 'webServer'];
export function apply(ctx, config) {
  if (!isAbsolute(config?.output ?? '')) throw new Error('Agent Watch needs an absolute private output path');
  if (config.worker && !['python', 'worker', 'controlConfig', 'log'].every(key => isAbsolute(config[key] ?? '')))
    throw new Error('Agent Watch worker paths must be absolute');
  let stopped = false, pending = Promise.resolve();
  let child;
  const publish = () => {
    pending = pending.then(async () => {
      if (stopped) return;
      const port = ctx.webServer.port;
      if (!Number.isInteger(port) || port < 1) return;
      const url = ctx.connection.authenticatedUrl(`http://127.0.0.1:${port}`);
      const tmp = `${config.output}.${process.pid}.tmp`;
      await writeFile(tmp, JSON.stringify({version: 1, url, pid: process.pid, updated: Date.now()}), {mode: 0o600});
      await rename(tmp, config.output);
    }).catch(() => { ctx.logger.warn('Agent Watch connection file could not be updated'); });
  };
  ctx.effect(() => {
    publish();
    const maintain = () => {
      publish();
      if (!config.worker || child || stopped) return;
      const fd = openSync(config.log, 'a', 0o600);
      try {
        child = spawn(config.python, [config.worker, '--config', config.controlConfig],
          {windowsHide: true, stdio: ['ignore', fd, fd]});
        const current = child;
        const clear = () => { if (child === current) child = undefined; };
        current.once('exit', clear); current.once('error', clear);
      } finally { closeSync(fd); }
    };
    const timer = setInterval(maintain, 3000);
    return async () => {
      stopped = true; clearInterval(timer);
      child?.kill();
      await pending;
      await unlink(config.output).catch(() => {});
    };
  });
}
