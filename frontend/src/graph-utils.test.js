import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildBrowserTree,
  buildGraphView,
  buildIndex,
  ignitionTagPathSegments,
} from './graph-utils.js';

test('Ignition tag paths become nested browser branches', () => {
  const tree = buildBrowserTree([
    ignitionTag('run', '[default]Area/Motor/Run'),
    ignitionTag('speed', '[default]Area/Motor/Speed'),
    ignitionTag('open', '[default]Area/Valve/Open'),
  ]);

  const tagGroup = tree[0].children[0];
  const provider = tagGroup.children[0];
  const area = provider.children[0];
  const motor = area.children.find((item) => item.label === 'Motor');
  const valve = area.children.find((item) => item.label === 'Valve');

  assert.equal(provider.label, '[default]');
  assert.equal(area.label, 'Area');
  assert.deepEqual(motor.children.map((item) => item.label), ['Run', 'Speed']);
  assert.deepEqual(valve.children.map((item) => item.label), ['Open']);
  assert.equal(motor.children[0].id, 'node:run');
});

test('Ignition provider and folder segments are parsed separately', () => {
  assert.deepEqual(
    ignitionTagPathSegments('[provider]Folder/Subfolder/Tag'),
    ['[provider]', 'Folder', 'Subfolder', 'Tag'],
  );
});

test('no canvas selection shows the root connection overview', () => {
  const model = {
    nodes: [
      { id: 'source', name: 'Controller Link', kind: 'SOURCE_DEVICE', system: 'SOURCE', attributes: {} },
      { id: 'target', name: 'DNP Connection', kind: 'IGNITION_DEVICE', system: 'IGNITION', attributes: {} },
    ],
    edges: [{
      id: 'match',
      source: 'source',
      target: 'target',
      kind: 'device_connection_match',
      status: 'resolved',
      attributes: {},
    }],
  };

  assert.deepEqual(buildGraphView(buildIndex(model), null, 'lineage', null), {
    nodeIds: ['source', 'target'],
    edgeIds: ['match'],
  });
});

function ignitionTag(id, name) {
  return { id, name, kind: 'IGNITION_TAG', system: 'IGNITION', attributes: {} };
}
