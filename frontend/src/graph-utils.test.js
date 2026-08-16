import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildBrowserTree,
  buildFlowElements,
  buildGraphView,
  buildIndex,
  findLineage,
  ignitionTagPathSegments,
  opcNodePathSegments,
  relationshipRows,
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

test('OPC variables are nested by connection and IEC variable path', () => {
  const node = {
    id: 'voltage',
    name: 'test_Meter_MODBUS.Voltage',
    kind: 'OPC_NODE',
    system: 'IGNITION',
    attributes: {
      connectionName: 'CODESYS_PLC_01',
      iecPath: 'Logic.Application.test_Meter_MODBUS.Voltage',
    },
  };
  assert.deepEqual(opcNodePathSegments(node), [
    'CODESYS_PLC_01', 'Logic', 'Application', 'test_Meter_MODBUS', 'Voltage',
  ]);

  const variableGroup = buildBrowserTree([node])[0].children[0];
  const connection = variableGroup.children[0];
  assert.equal(connection.label, 'CODESYS_PLC_01');
  assert.equal(connection.children[0].label, 'Logic');
  assert.equal(
    connection.children[0].children[0].children[0].children[0].id,
    'node:voltage',
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

test('third-party OPC server connections appear in the connection overview', () => {
  const model = {
    nodes: [{
      id: 'codesys',
      name: 'CODESYS PLC',
      kind: 'OPC_SERVER_CONNECTION',
      system: 'IGNITION',
      attributes: {},
    }],
    edges: [],
  };

  assert.deepEqual(buildGraphView(buildIndex(model), null, 'lineage', null), {
    nodeIds: ['codesys'],
    edgeIds: [],
  });
});

test('selecting an OPC connection keeps its signals off the canvas and in the table', () => {
  const nodes = [{
    id: 'codesys', name: 'CODESYS PLC', kind: 'OPC_SERVER_CONNECTION', system: 'IGNITION', attributes: {},
  }];
  const edges = [];
  for (let position = 0; position < 250; position += 1) {
    nodes.push({
      id: `signal-${position}`,
      name: `Signal ${position}`,
      kind: 'OPC_NODE',
      system: 'IGNITION',
      attributes: {},
    });
    edges.push({
      id: `provides-${position}`,
      source: 'codesys',
      target: `signal-${position}`,
      kind: 'provides',
      status: 'resolved',
      attributes: {},
    });
  }
  const index = buildIndex({ nodes, edges });
  assert.deepEqual(buildGraphView(index, 'codesys', 'lineage', null), {
    nodeIds: ['codesys'],
    edgeIds: [],
  });
  assert.equal(relationshipRows(index, 'codesys', []).length, 200);
});

test('a selected OPC signal lineage does not pull in sibling variables', () => {
  const model = {
    nodes: [
      { id: 'source', name: 'PLC', kind: 'SOURCE_DEVICE', system: 'SOURCE', attributes: {} },
      { id: 'connection', name: 'PLC OPC', kind: 'OPC_SERVER_CONNECTION', system: 'IGNITION', attributes: {} },
      { id: 'one', name: 'One', kind: 'OPC_NODE', system: 'IGNITION', attributes: {} },
      { id: 'two', name: 'Two', kind: 'OPC_NODE', system: 'IGNITION', attributes: {} },
      { id: 'tag-one', name: '[default]One', kind: 'IGNITION_TAG', system: 'IGNITION', attributes: {} },
      { id: 'tag-two', name: '[default]Two', kind: 'IGNITION_TAG', system: 'IGNITION', attributes: {} },
    ],
    edges: [
      { id: 'match', source: 'source', target: 'connection', kind: 'device_connection_match', status: 'resolved' },
      { id: 'provide-one', source: 'connection', target: 'one', kind: 'provides', status: 'resolved' },
      { id: 'provide-two', source: 'connection', target: 'two', kind: 'provides', status: 'resolved' },
      { id: 'drive-one', source: 'one', target: 'tag-one', kind: 'drives', status: 'resolved' },
      { id: 'drive-two', source: 'two', target: 'tag-two', kind: 'drives', status: 'resolved' },
    ],
  };
  const index = buildIndex(model);
  const lineage = findLineage(index, 'one');
  const view = buildGraphView(index, 'one', 'lineage', lineage);
  assert.ok(view.nodeIds.includes('one'));
  assert.ok(view.nodeIds.includes('tag-one'));
  assert.ok(!view.nodeIds.includes('two'));
  assert.ok(!view.nodeIds.includes('tag-two'));

  const upstream = buildGraphView(index, 'tag-one', 'upstream', null);
  assert.ok(upstream.nodeIds.includes('connection'));
  assert.ok(!upstream.nodeIds.includes('two'));
  const filteredFlow = buildFlowElements(
    index,
    upstream,
    'tag-one',
    ['IGNITION'],
    ['Protocol point', 'Tag'],
  );
  assert.ok(filteredFlow.nodes.some((node) => node.id === 'connection'));
});

test('connection overview enforces the canvas node budget', () => {
  const nodes = Array.from({ length: 140 }, (_, position) => ({
    id: `connection-${position}`,
    name: `Connection ${position}`,
    kind: 'OPC_SERVER_CONNECTION',
    system: 'IGNITION',
    attributes: {},
  }));
  const view = buildGraphView(buildIndex({ nodes, edges: [] }), null, 'lineage', null);
  assert.equal(view.nodeIds.length, 100);
  assert.equal(view.truncated, true);
  assert.equal(view.nodeLimit, 100);
});

function ignitionTag(id, name) {
  return { id, name, kind: 'IGNITION_TAG', system: 'IGNITION', attributes: {} };
}
