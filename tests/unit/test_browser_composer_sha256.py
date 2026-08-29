"""Portable worker SHA-256 keeps digest binding on non-secure local origins."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHA256_MODULE = ROOT / "web/static/js/composer_sha256.js"
PROFILE = ROOT / "tests/fixtures/installation_profile_v1.bin"


class BrowserComposerSha256Tests(unittest.TestCase):
    def test_portable_and_webcrypto_paths_match_standard_vectors_and_lgip(self) -> None:
        script = r"""
const assert = require('assert');
const fs = require('fs');
const nodeCrypto = require('crypto');

(async () => {
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  const sha = await import(moduleUrl);
  const hex = (bytes) => Buffer.from(bytes).toString('hex');
  const vectors = [
    Buffer.alloc(0),
    Buffer.from('abc'),
    Buffer.from('The quick brown fox jumps over the lazy dog'),
    Buffer.from(Array.from({length: 4097}, (_, index) => index & 0xff)),
  ];
  for (const value of vectors) {
    const expected = nodeCrypto.createHash('sha256').update(value).digest('hex');
    assert.strictEqual(hex(sha.sha256Portable(value)), expected);
    assert.strictEqual(hex(await sha.sha256Bytes(value, {})), expected);
    assert.strictEqual(
      hex(await sha.sha256Bytes(value, nodeCrypto.webcrypto)),
      expected,
    );
  }

  const profile = Uint8Array.from(fs.readFileSync(process.argv[2]));
  const embedded = hex(profile.slice(68, 100));
  profile.fill(0, 68, 100);
  assert.strictEqual(hex(await sha.sha256Bytes(profile, {})), embedded);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
        completed = subprocess.run(
            ["node", "-e", script, str(SHA256_MODULE), str(PROFILE)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
