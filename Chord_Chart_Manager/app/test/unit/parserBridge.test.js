import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { bridgeSharedParserSource } from '../../vite.config.js';

const parserPath = fileURLToPath(new URL('../../server/chartParser.js', import.meta.url));
const parserSource = fs.readFileSync(parserPath, 'utf8');

describe('shared chart parser bridge', () => {
  it('NFR-012 removes only the CommonJS branch from the shared parser', () => {
    const bridged = bridgeSharedParserSource(parserSource);
    expect(bridged).toContain('  root.ChartParser = factory();');
    expect(bridged).not.toContain('module.exports = factory()');
    expect(bridged.length).toBeLessThan(parserSource.length);
  });

  it('NFR-012 accepts the same header with CRLF line endings', () => {
    const bridged = bridgeSharedParserSource(parserSource.replace(/\r?\n/g, '\r\n'));
    expect(bridged).not.toContain('module.exports = factory()');
  });

  it('NFR-012 fails loudly when the UMD header no longer matches', () => {
    const changed = parserSource.replace('module.exports = factory();', 'module.exports = factory(root);');
    expect(() => bridgeSharedParserSource(changed)).toThrow(/UMD header/);
  });
});
