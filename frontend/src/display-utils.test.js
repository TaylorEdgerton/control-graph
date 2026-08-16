import assert from 'node:assert/strict';
import test from 'node:test';

import { flattenKeyValuePairs, formatAttributeKey } from './display-utils.js';

test('nested resolver attributes become readable key value rows', () => {
  const rows = flattenKeyValuePairs({
    identityKey: 'dnp3|10.20.1.20|1|binary input|12',
    matchedPoints: ['point-1', 'point-2'],
    device: { protocol: 'DNP3', enabled: true },
  });

  assert.deepEqual(rows, [
    { key: 'device.enabled', value: 'True' },
    { key: 'device.protocol', value: 'DNP3' },
    { key: 'identityKey', value: 'dnp3|10.20.1.20|1|binary input|12' },
    { key: 'matchedPoints[0]', value: 'point-1' },
    { key: 'matchedPoints[1]', value: 'point-2' },
  ]);
  assert.equal(formatAttributeKey('matchedPoints[0]'), 'Matched Points › 0');
});
