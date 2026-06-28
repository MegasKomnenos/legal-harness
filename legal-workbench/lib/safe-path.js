const path = require('path');

const PROJECT_DIR = path.resolve(__dirname, '..', '..');

const PROTECTED_DIRS = ['harness', '법령_판례'];

function resolve(relativePath) {
  const resolved = path.resolve(PROJECT_DIR, relativePath);
  if (!resolved.startsWith(PROJECT_DIR + path.sep) && resolved !== PROJECT_DIR) {
    const err = new Error('Path traversal blocked');
    err.status = 403;
    throw err;
  }
  return resolved;
}

function isProtected(absPath) {
  const rel = path.relative(PROJECT_DIR, absPath);
  return PROTECTED_DIRS.some(d => rel === d || rel.startsWith(d + path.sep));
}

module.exports = { PROJECT_DIR, resolve, isProtected };
