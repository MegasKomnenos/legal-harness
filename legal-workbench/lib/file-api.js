const express = require('express');
const fs = require('fs/promises');
const fsSync = require('fs');
const path = require('path');
const safePath = require('./safe-path');

function createRouter() {
  const router = express.Router();

  router.get('/files/list', async (req, res) => {
    try {
      const dir = req.query.dir || '';
      const abs = safePath.resolve(dir);
      const entries = await fs.readdir(abs, { withFileTypes: true });

      const result = [];
      for (const e of entries) {
        if (e.name.startsWith('.') || e.name === 'node_modules' || e.name === '__pycache__' || e.name === 'legal-workbench' || e.name === 'webapp') continue;
        const fullPath = path.join(abs, e.name);
        const isDir = e.isDirectory();
        let size = 0;
        let mtime = null;
        if (!isDir) {
          try {
            const st = await fs.stat(fullPath);
            size = st.size;
            mtime = st.mtime.toISOString();
          } catch {}
        }
        result.push({
          name: e.name,
          type: isDir ? 'dir' : 'file',
          size,
          mtime,
          path: path.relative(safePath.PROJECT_DIR, fullPath),
        });
      }

      result.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
        return a.name.localeCompare(b.name, 'ko');
      });

      res.json({ entries: result });
    } catch (err) {
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  router.get('/files/read', async (req, res) => {
    try {
      const filePath = req.query.path;
      if (!filePath) return res.status(400).json({ error: 'path required' });
      const abs = safePath.resolve(filePath);
      const st = await fs.stat(abs);
      if (st.isDirectory()) return res.status(400).json({ error: 'Cannot read directory' });
      if (st.size > 5 * 1024 * 1024) return res.status(413).json({ error: 'File too large (>5MB)' });
      const content = await fs.readFile(abs, 'utf-8');
      res.json({ content, size: st.size, path: filePath });
    } catch (err) {
      if (err.code === 'ENOENT') return res.status(404).json({ error: 'Not found' });
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  router.get('/files/download', async (req, res) => {
    try {
      const filePath = req.query.path;
      if (!filePath) return res.status(400).json({ error: 'path required' });
      const abs = safePath.resolve(filePath);
      const st = await fs.stat(abs);
      if (st.isDirectory()) return res.status(400).json({ error: 'Cannot download directory' });
      res.download(abs, path.basename(abs));
    } catch (err) {
      if (err.code === 'ENOENT') return res.status(404).json({ error: 'Not found' });
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  router.post('/files/write', async (req, res) => {
    try {
      const { path: filePath, content } = req.body;
      if (!filePath || content === undefined) {
        return res.status(400).json({ error: 'path and content required' });
      }
      const abs = safePath.resolve(filePath);
      if (safePath.isProtected(abs)) {
        return res.status(403).json({ error: 'Protected path: write not allowed' });
      }
      if (Buffer.byteLength(content, 'utf-8') > 2 * 1024 * 1024) {
        return res.status(413).json({ error: 'Content too large (>2MB)' });
      }
      await fs.mkdir(path.dirname(abs), { recursive: true });
      await fs.writeFile(abs, content, 'utf-8');
      res.json({ ok: true });
    } catch (err) {
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  router.post('/files/mkdir', async (req, res) => {
    try {
      const { path: dirPath } = req.body;
      if (!dirPath) return res.status(400).json({ error: 'path required' });
      const abs = safePath.resolve(dirPath);
      if (safePath.isProtected(abs)) {
        return res.status(403).json({ error: 'Protected path: mkdir not allowed' });
      }
      await fs.mkdir(abs, { recursive: true });
      res.json({ ok: true });
    } catch (err) {
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  router.delete('/files/delete', async (req, res) => {
    try {
      const filePath = req.query.path;
      if (!filePath) return res.status(400).json({ error: 'path required' });
      const abs = safePath.resolve(filePath);
      if (safePath.isProtected(abs)) {
        return res.status(403).json({ error: 'Protected path: delete not allowed' });
      }
      const st = await fs.stat(abs);
      if (st.isDirectory()) {
        await fs.rmdir(abs);
      } else {
        await fs.unlink(abs);
      }
      res.json({ ok: true });
    } catch (err) {
      if (err.code === 'ENOENT') return res.status(404).json({ error: 'Not found' });
      res.status(err.status || 500).json({ error: err.message });
    }
  });

  return router;
}

module.exports = createRouter;
