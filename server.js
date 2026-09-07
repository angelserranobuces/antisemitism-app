const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');

// ── Put your Anthropic API key here ───────────────────────────────────────
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || 'YOUR_API_KEY_HERE';
const PORT = 3000;
const MAX_BODY = 100 * 1024 * 1024; // 100 MB — handles large scanned newspaper PDFs

// ── MIME types ─────────────────────────────────────────────────────────────
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.ico':  'image/x-icon',
};

function serveFile(res, filePath) {
  fs.readFile(filePath, function(err, data) {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    var ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'text/plain' });
    res.end(data);
  });
}

function proxyToAnthropic(req, res) {
  var chunks = [];
  var size = 0;

  req.on('data', function(chunk) {
    size += chunk.length;
    if (size > MAX_BODY) {
      res.writeHead(413, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'PDF too large (max 50 MB).' } }));
      req.destroy();
      return;
    }
    chunks.push(chunk);
  });

  req.on('end', function() {
    var bodyStr = Buffer.concat(chunks).toString();

    var options = {
      hostname: 'api.anthropic.com',
      path:     '/v1/messages',
      method:   'POST',
      headers: {
        'Content-Type':      'application/json',
        'x-api-key':         ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Length':    Buffer.byteLength(bodyStr),
      }
    };

    var apiReq = https.request(options, function(apiRes) {
      var resp = [];
      apiRes.on('data', function(c) { resp.push(c); });
      apiRes.on('end', function() {
        res.writeHead(apiRes.statusCode, {
          'Content-Type':                'application/json',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(Buffer.concat(resp).toString());
      });
    });

    apiReq.on('error', function(err) {
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
      'Access-Control-Allow-Origin':  '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
  }

  // API proxy
  if (req.method === 'POST' && req.url === '/api/messages') {
    proxyToAnthropic(req, res);
    return;
  }

  // Static files
  var urlPath = (req.url === '/' || req.url === '') ? '/index.html' : req.url;
  serveFile(res, path.join(__dirname, urlPath));

}).listen(PORT, function() {
  console.log('');
  console.log('  ✓  Media Framing Risk Analyser is running');
  console.log('');
  console.log('     Open this in your browser:');
  console.log('     → http://localhost:' + PORT);
  console.log('');
  console.log('     Press Ctrl+C to stop.');
  console.log('');
});
