const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || '';
const PORT = process.env.PORT || 3000;
const MAX_BODY = 100 * 1024 * 1024;

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
  var chunks = [], size = 0;

  req.on('data', function(chunk) {
    size += chunk.length;
    if (size > MAX_BODY) {
      res.writeHead(413, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'File too large (max 100 MB).' } }));
      req.destroy(); return;
    }
    chunks.push(chunk);
  });

  req.on('end', function() {
    if (res.destroyed) return;
    var bodyStr = Buffer.concat(chunks).toString();
    console.log('[API] Request size:', Math.round(size/1024), 'KB');

    if (!ANTHROPIC_API_KEY) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'API key not set on server' } }));
      return;
    }

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
        var body = Buffer.concat(resp).toString();
        console.log('[API] Anthropic status:', apiRes.statusCode);
        // Always log full response for debugging
        // Log full response for debugging (truncated to 2000 chars)
console.log('[API] Anthropic response:', body.slice(0, 2000));
try {
  var parsed = JSON.parse(body);
  console.log('[API] stop_reason:', parsed.stop_reason);
  if (parsed.content && parsed.content[0]) {
    console.log('[API] content text length:', (parsed.content[0].text||'').length);
    console.log('[API] content last 200:', (parsed.content[0].text||'').slice(-200));
  }
} catch(e) { console.log('[API] Could not parse response as JSON'); }
        res.writeHead(apiRes.statusCode, {
          'Content-Type':                'application/json',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(body);
      });
    });

    apiReq.on('error', function(err) {
      console.error('[API] Request error:', err.message);
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'Proxy error: ' + err.message } }));
    });

    apiReq.write(bodyStr);
    apiReq.end();
  });
}

http.createServer(function(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin':  '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end(); return;
  }
  if (req.method === 'POST' && req.url === '/api/messages') {
    proxyToAnthropic(req, res); return;
  }
  var urlPath = (req.url === '/' || req.url === '') ? '/index.html' : req.url;
  serveFile(res, path.join(__dirname, urlPath));

}).listen(PORT, function() {
  console.log('Media Framing Risk Analyser running on port ' + PORT);
  console.log('API key set: YES (length:', ANTHROPIC_API_KEY.length + ')');
});
