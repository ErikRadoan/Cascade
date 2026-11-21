// Tests for material colour resolution helpers (pure module, no Svelte runes).
//   npm run test:unit
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeHex, hexToRgb01, rgb01ToHex, hashColor,
  defaultMaterialColorHex, defaultMaterialColorRgb01, lookupOverride, MATERIAL_COLORS,
} from '../src/lib/components/geometry/materialColors.ts';

test('normalizeHex canonicalises and rejects junk', () => {
  assert.equal(normalizeHex('#e8703a'), '#E8703A');
  assert.equal(normalizeHex('E8703A'), '#E8703A');
  assert.equal(normalizeHex(' #abc '), '#AABBCC');
  for (const bad of ['', 'red', '#12345', '#GGGGGG', '#1234567', null, undefined]) {
    assert.equal(normalizeHex(bad as string | null | undefined), null, String(bad));
  }
});

test('hex <-> rgb01 round trip', () => {
  for (const hex of ['#000000', '#FFFFFF', '#E8703A', '#4A90D9', '#0A0B0C']) {
    assert.equal(rgb01ToHex(hexToRgb01(hex)), hex);
  }
  assert.deepEqual(hexToRgb01('#FF0000'), [1, 0, 0]);
});

test('default colour: palette exact, palette case-insensitive, hash fallback', () => {
  assert.equal(defaultMaterialColorHex('UO2'), MATERIAL_COLORS['UO2']);
  assert.equal(defaultMaterialColorHex('uo2'), MATERIAL_COLORS['UO2']);
  const h1 = defaultMaterialColorHex('MY_UNKNOWN_ALLOY');
  assert.match(h1, /^#[0-9A-F]{6}$/);
  assert.equal(h1, defaultMaterialColorHex('MY_UNKNOWN_ALLOY'));          // stable
  assert.deepEqual(defaultMaterialColorRgb01('MY_UNKNOWN_ALLOY'), hashColor('MY_UNKNOWN_ALLOY'));
  assert.notEqual(h1, defaultMaterialColorHex('ANOTHER_ONE'));
});

test('lookupOverride: exact, case-insensitive, invalid ignored, miss -> null', () => {
  const o = { UO2: '#010203', Water: '#0a0b0c', Bad: 'nope' };
  assert.equal(lookupOverride(o, 'UO2'), '#010203');
  assert.equal(lookupOverride(o, 'uo2'), '#010203');
  assert.equal(lookupOverride(o, 'water'), '#0A0B0C');
  assert.equal(lookupOverride(o, 'Bad'), null);
  assert.equal(lookupOverride(o, 'Zr4'), null);
  assert.equal(lookupOverride({}, 'UO2'), null);
});
