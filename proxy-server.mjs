import http from 'http';
import { createReadStream, existsSync, statSync } from 'fs';
import { join, extname } from 'path';

const FRONTEND_DIR = '/home/staz/Downloads/myProjetctKufar/frontend';
const API_PORT = 8010;
const PORT = 8081;

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
