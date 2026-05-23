import http from 'http';
import { createReadStream, existsSync, statSync } from 'fs';
import { join, extname, dirname } from 'path';
import { fileURLToPath } from 'url';

// INF-M2: previously hardcoded as `/home/staz/Downloads/myProjetctKufar/frontend`
// which only worked on a single developer's machine. Resolve relative to
// this file so the dev proxy works from any clone or CI checkout. Override
// via FRONTEND_DIR env var when running outside the repo layout.
const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = process.env.FRONTEND_DIR || join(__dirname, 'frontend');
const API_PORT = Number(process.env.API_PORT) || 8010;
const PORT = Number(process.env.PORT) || 8081;

const MIME = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff',
  '.webp': 'image/webp', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  
  if (url.pathname.startsWith('/api/')) {
    const options = {
      hostname: 'localhost', port: API_PORT,
      path: url.pathname + url.search, method: req.method,
      headers: { ...req.headers, host: `localhost:${API_PORT}` },
    };
    const proxy = http.request(options, (pRes) => {
      res.writeHead(pRes.statusCode, pRes.headers);
      pRes.pipe(res);
    });
    proxy.on('error', () => { res.writeHead(502); res.end('Bad Gateway'); });
    req.pipe(proxy);
    return;
  }

  let filePath = join(FRONTEND_DIR, url.pathname === '/' ? 'index.html' : url.pathname);
  if (!existsSync(filePath)) { res.writeHead(404); res.end('Not Found'); return; }
  const ext = extname(filePath);
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
  createReadStream(filePath).pipe(res);
});

server.listen(PORT, () => console.log(`Proxy on :${PORT}`));
