const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || '';
const PORT = process.env.PORT || 3000;
const MAX_BODY = 800 * 1024 * 1024;
const DATA_FILE = path.join(__dirname, 'data.json');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.ico':  'image/x-icon',
};

// ── Data persistence helpers ───────────────────────────────────────────────
function loadData() {
  try {
    if (fs.existsSync(DATA_FILE)) {
      return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
    }
  } catch(e) { console.error('Error loading data:', e.message); }
  return [];
}

function saveData(entries) {
  try {
    fs.writeFileSync(DATA_FILE, JSON.stringify(entries), 'utf8');
  } catch(e) { console.error('Error saving data:', e.message); }
}

// ── Static file server ─────────────────────────────────────────────────────
function serveFile(res, filePath) {
  fs.readFile(filePath, function(err, data) {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    var ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'text/plain' });
    res.end(data);
  });
}

// ── Body reader ────────────────────────────────────────────────────────────
function readBody(req, res, cb) {
  var chunks = [], size = 0;
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

// ── Anthropic proxy ────────────────────────────────────────────────────────
function proxyToAnthropic(req, res) {
  readBody(req, res, function(bodyStr) {
    console.log('[API] Request size:', Math.round(bodyStr.length/1024), 'KB');
    if (!ANTHROPIC_API_KEY) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'API key not configured on server.' } }));
      return;
    }
    var options = {
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
    var apiReq = https.request(options, function(apiRes) {
      var resp = [];
      apiRes.on('data', function(c) { resp.push(c); });
      apiRes.on('end', function() {
        var body = Buffer.concat(resp).toString();
        console.log('[API] Status:', apiRes.statusCode, '| Response length:', body.length);
        res.writeHead(apiRes.statusCode, {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(body);
      });
    });
    apiReq.on('error', function(err) {
      console.error('[API] Error:', err.message);
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'Proxy error: ' + err.message } }));
    });
    apiReq.write(bodyStr);
    apiReq.end();
  });
}

// ── HTTP server ────────────────────────────────────────────────────────────
http.createServer(function(req, res) {

  // CORS preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS, DELETE',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end(); return;
  }

  var url = req.url.split('?')[0];

  // ── GET /api/history — load all entries ──
  if (req.method === 'GET' && url === '/api/history') {
    var data = loadData();
    res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify(data));
    return;
  }

  // ── POST /api/history — save a new entry ──
  if (req.method === 'POST' && url === '/api/history') {
    readBody(req, res, function(bodyStr) {
      try {
        var entry = JSON.parse(bodyStr);
        var data = loadData();
        // Avoid duplicates by id
        data = data.filter(function(e) { return String(e.id) !== String(entry.id); });
        data.push(entry);
        saveData(data);
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ ok: true, count: data.length }));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: { message: 'Invalid JSON' } }));
      }
    });
    return;
  }

  // ── DELETE /api/history/:id — delete one entry ──
  if (req.method === 'DELETE' && url.startsWith('/api/history/')) {
    var id = url.replace('/api/history/', '');
    var data = loadData().filter(function(e) { return String(e.id) !== id; });
    saveData(data);
    res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // ── DELETE /api/history — clear all ──
  if (req.method === 'DELETE' && url === '/api/history') {
    saveData([]);
    res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // ── POST /api/messages — Anthropic proxy ──
  if (req.method === 'POST' && url === '/api/messages') {
    proxyToAnthropic(req, res); return;
  }

  // ── Static files ──
  var filePath = (url === '/' || url === '') ? '/index.html' : url;
  serveFile(res, path.join(__dirname, filePath));

}).listen(PORT, function() {
  console.log('Media Framing Risk Analyser running on port ' + PORT);
  console.log('API key set: YES (length:', ANTHROPIC_API_KEY.length + ')');
  console.log('Data file:', DATA_FILE);
});
