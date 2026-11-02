// Vercel build for the static frontend (static/). No bundler: it only writes static/config.js with the
// Cloud Run API URL (VITE_API_BASE). config.js is gitignored and regenerated on every build.
const fs = require('node:fs');
const path = require('node:path');

const apiBase = (process.env.VITE_API_BASE || 'http://localhost:8000').trim().replace(/\/+$/, '');
fs.writeFileSync(path.join(__dirname, 'static', 'config.js'), `window.CARTPILOT_API_BASE = ${JSON.stringify(apiBase)};\n`);
console.log(`Frontend built: API at ${apiBase}`);
