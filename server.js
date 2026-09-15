const http   = require('http');
const https  = require('https');
const fs     = require('fs');
const path   = require('path');
const { Client } = require('pg');

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || '';
const DATABASE_URL      = process.env.DATABASE_URL || '';
const PORT              = process.env.PORT || 3000;
const MAX_BODY          = 800 * 1024 * 1024;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.ico':  'image/x-icon',
};

// ── DB helpers ─────────────────────────────────────────────────────────────
async function getClient() {
  const client = new Client({
    connectionString: DATABASE_URL,
    ssl: { rejectUnauthorized: false }
  });
  await client.connect();
  return client;
}

async function initDB() {
  if (!DATABASE_URL) { console.log('No DATABASE_URL — skipping DB init'); return; }
  const client = await getClient();
  await client.query(`
    CREATE TABLE IF NOT EXISTS entries (
      id TEXT PRIMARY KEY,
      data JSONB NOT NULL,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
  await client.end();
  console.log('Database ready');
}

async function dbGetAll() {
  if (!DATABASE_URL) return [];
  const client = await getClient();
  const res = await client.query('SELECT data FROM entries ORDER BY created_at ASC');
  await client.end();
  return res.rows.map(function(r){ return r.data; });
}

async function dbUpsert(entry) {
  if (!DATABASE_URL) return;
  const client = await getClient();
  await client.query(
    'INSERT INTO entries (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = $2',
    [String(entry.id), JSON.stringify(entry)]
  );
  await client.end();
}

async function dbDelete(id) {
  if (!DATABASE_URL) return;
  const client = await getClient();
  await client.query('DELETE FROM entries WHERE id = $1', [String(id)]);
  await client.end();
}

async function dbClear() {
  if (!DATABASE_URL) return;
  const client = await getClient();
  await client.query('DELETE FROM entries');
  await client.end();
}

// ── Static files ───────────────────────────────────────────────────────────
function serveFile(res, filePath) {
  fs.readFile(filePath, function(err, data) {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'text/plain' });
    res.end(data);
  });
}

// ── Body reader ────────────────────────────────────────────────────────────
function readBody(req, res, cb) {
  const chunks = []; let size = 0;
  req.on('data', function(chunk) {
    size += chunk.length;
    if (size > MAX_BODY) {
      res.writeHead(413, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'Request too large.' } }));
      req.destroy(); return;
    }
    chunks.push(chunk);
  });
  req.on('end', function() {
    if (res.destroyed) return;
    cb(Buffer.concat(chunks).toString());
  });
}

// ── JSON response ──────────────────────────────────────────────────────────
function jsonRes(res, status, obj) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
  res.end(JSON.stringify(obj));
}

// ── Anthropic proxy ────────────────────────────────────────────────────────
function proxyToAnthropic(req, res) {
  readBody(req, res, function(bodyStr) {
    console.log('[API] Request size:', Math.round(bodyStr.length/1024), 'KB');
    if (!ANTHROPIC_API_KEY) { jsonRes(res, 500, { error: { message: 'API key not configured.' } }); return; }
    const options = {
      hostname: 'api.anthropic.com',
      path: '/v1/messages',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Length': Buffer.byteLength(bodyStr),
      }
    };
    const apiReq = https.request(options, function(apiRes) {
      const resp = [];
      apiRes.on('data', function(c){ resp.push(c); });
      apiRes.on('end', function() {
        const body = Buffer.concat(resp).toString();
        console.log('[API] Status:', apiRes.statusCode);
        res.writeHead(apiRes.statusCode, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(body);
      });
    });
    apiReq.on('error', function(err) {
      jsonRes(res, 502, { error: { message: 'Proxy error: ' + err.message } });
    });
    apiReq.write(bodyStr);
    apiReq.end();
  });
}

// ── HTTP server ────────────────────────────────────────────────────────────
const server = http.createServer(function(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS, DELETE',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end(); return;
  }

  const url = req.url.split('?')[0];

  // GET /api/history
  if (req.method === 'GET' && url === '/api/history') {
    dbGetAll().then(function(data){ jsonRes(res, 200, data); })
      .catch(function(e){ console.error('dbGetAll error:', e.message); jsonRes(res, 500, { error: { message: e.message } }); });
    return;
  }

  // POST /api/history
  if (req.method === 'POST' && url === '/api/history') {
    readBody(req, res, function(bodyStr) {
      try {
        const entry = JSON.parse(bodyStr);
        dbUpsert(entry).then(function(){ jsonRes(res, 200, { ok: true }); })
          .catch(function(e){ console.error('dbUpsert error:', e.message); jsonRes(res, 500, { error: { message: e.message } }); });
      } catch(e) { jsonRes(res, 400, { error: { message: 'Invalid JSON' } }); }
    });
    return;
  }

  // DELETE /api/history/:id
  if (req.method === 'DELETE' && url.startsWith('/api/history/')) {
    const id = decodeURIComponent(url.replace('/api/history/', ''));
    dbDelete(id).then(function(){ jsonRes(res, 200, { ok: true }); })
      .catch(function(e){ jsonRes(res, 500, { error: { message: e.message } }); });
    return;
  }

  // DELETE /api/history
  if (req.method === 'DELETE' && url === '/api/history') {
    dbClear().then(function(){ jsonRes(res, 200, { ok: true }); })
      .catch(function(e){ jsonRes(res, 500, { error: { message: e.message } }); });
    return;
  }

  // POST /api/messages
  if (req.method === 'POST' && url === '/api/messages') {
    proxyToAnthropic(req, res); return;
  }

  // Static files
  const filePath = (url === '/' || url === '') ? '/index.html' : url;
  serveFile(res, path.join(__dirname, filePath));
});

// ── Start ──────────────────────────────────────────────────────────────────
initDB().then(function() {
  server.listen(PORT, function() {
    console.log('Media Framing Risk Analyser running on port ' + PORT);
    console.log('Database:', DATABASE_URL ? 'PostgreSQL connected' : 'NOT configured');
    console.log('API key:', ANTHROPIC_API_KEY ? 'set (length ' + ANTHROPIC_API_KEY.length + ')' : 'NOT set');
  });
}).catch(function(e) {
  console.error('DB init failed:', e.message);
  // Start anyway without DB
  server.listen(PORT, function() {
    console.log('Running WITHOUT database on port ' + PORT);
  });
});
