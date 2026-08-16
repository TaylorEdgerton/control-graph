import dagre from '@dagrejs/dagre';
import { MarkerType } from '@xyflow/react';
import {
  FLOW_NODE_HEIGHT,
  FLOW_NODE_WIDTH,
  KIND_LABELS,
  SYSTEM_FILTERS,
  TYPE_FILTERS,
} from './constants.js';
import { darkTokens } from './theme-tokens.js';

export function buildBrowserTree(nodes) {
  const systems = new Map();
  for (const node of nodes) {
    if (!systems.has(node.system)) systems.set(node.system, new Map());
    const groups = systems.get(node.system);
    if (!groups.has(node.kind)) groups.set(node.kind, []);
    groups.get(node.kind).push(node);
  }
  const systemOrder = ['SOURCE', 'IGNITION', 'RESOLVER'];
  return [...systems.entries()]
    .sort(([left], [right]) => {
      const leftOrder = systemOrder.indexOf(left);
      const rightOrder = systemOrder.indexOf(right);
      return (leftOrder < 0 ? 99 : leftOrder) - (rightOrder < 0 ? 99 : rightOrder) || left.localeCompare(right);
    })
    .map(([system, groups]) => {
      const count = [...groups.values()].reduce((total, items) => total + items.length, 0);
      const systemLabel = SYSTEM_FILTERS.find((item) => item.value === system)?.label || system;
      return {
        id: `system:${system}`,
        label: `${systemLabel} (${count})`,
        children: [...groups.entries()]
          .sort(([left], [right]) => (KIND_LABELS[left] || left).localeCompare(KIND_LABELS[right] || right))
          .map(([kind, items]) => ({
            id: `group:${system}:${kind}`,
            label: `${KIND_LABELS[kind] || kind} (${items.length})`,
            children: kind === 'IGNITION_TAG'
              ? buildIgnitionTagTree(items, system, kind)
              : items
                .sort((left, right) => left.name.localeCompare(right.name))
                .map((node) => ({ id: `node:${node.id}`, label: shortName(node.name) })),
          })),
      };
    });
}

export function ignitionTagPathSegments(name) {
  const value = String(name || '').trim();
  const provider = value.match(/^(\[[^\]]+\])/u);
  const remainder = provider ? value.slice(provider[1].length) : value;
  return [
    ...(provider ? [provider[1]] : []),
    ...remainder.split('/').map((part) => part.trim()).filter(Boolean),
  ];
}

function buildIgnitionTagTree(nodes, system, kind) {
  const root = createTagBranch('');
  for (const node of [...nodes].sort((left, right) => left.name.localeCompare(right.name))) {
    const segments = ignitionTagPathSegments(node.name);
    let branch = root;
    for (const segment of segments.length ? segments : [node.name]) {
      if (!branch.children.has(segment)) branch.children.set(segment, createTagBranch(segment));
      branch = branch.children.get(segment);
    }
    branch.nodes.push(node);
  }
  return tagBranchItems(root, system, kind, []);
}

function createTagBranch(label) {
  return { label, children: new Map(), nodes: [] };
}

function tagBranchItems(branch, system, kind, parentPath) {
  return [...branch.children.values()]
    .sort((left, right) => left.label.localeCompare(right.label))
    .map((child) => {
      const path = [...parentPath, child.label];
      const descendants = tagBranchItems(child, system, kind, path);
      if (child.nodes.length === 1) {
        return {
          id: `node:${child.nodes[0].id}`,
          label: child.label,
          ...(descendants.length ? { children: descendants } : {}),
        };
      }
      const duplicateNodes = child.nodes.map((node) => ({
        id: `node:${node.id}`,
        label: `${child.label} · ${sourceName(node)}`,
      }));
      return {
        id: `folder:${system}:${kind}:${path.map(encodeURIComponent).join('/')}`,
        label: child.label,
        children: [...descendants, ...duplicateNodes],
      };
    });
}

export function collectBranchIds(items) {
  const result = [];
  for (const item of items) {
    if (!item.children?.length) continue;
    result.push(item.id, ...collectBranchIds(item.children));
  }
  return result;
}

export function buildIndex(model) {
  const nodes = new Map((model?.nodes || []).map((node) => [node.id, node]));
  const edges = new Map((model?.edges || []).map((edge) => [edge.id, edge]));
  const outgoing = new Map();
  const incoming = new Map();
  for (const edge of edges.values()) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    outgoing.get(edge.source).push(edge);
    incoming.get(edge.target).push(edge);
  }
  return { nodes, edges, outgoing, incoming };
}

export function searchNodes(nodes, query, systemFilters, typeFilters) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const selectedKinds = TYPE_FILTERS.filter((group) => typeFilters.includes(group.label)).flatMap((group) => group.kinds);
  return nodes.filter((node) => {
    if (systemFilters.length && !systemFilters.includes(node.system)) return false;
    if (selectedKinds.length && !selectedKinds.includes(node.kind)) return false;
    const haystack = `${node.name} ${node.kind} ${node.system} ${JSON.stringify(node.attributes)}`.toLocaleLowerCase();
    return terms.every((term) => haystack.includes(term));
  }).sort((a, b) => a.name.localeCompare(b.name));
}

export function findLineage(index, selectedId) {
  if (!selectedId || !index.nodes.has(selectedId)) return null;
  const component = connectedNodes(index, selectedId);
  const starts = [...component].filter((id) => index.nodes.get(id)?.kind === 'SOURCE_DEVICE');
  const ends = new Set([...component].filter((id) => index.nodes.get(id)?.kind === 'IGNITION_TAG'));
  const paths = [];
  for (const start of starts) {
    const queue = [{ nodeId: start, nodeIds: [start], edgeIds: [] }];
    const bestDepth = new Map([[start, 0]]);
    while (queue.length) {
      const current = queue.shift();
      if (ends.has(current.nodeId)) paths.push(current);
      if (current.nodeIds.length > 30) continue;
      for (const edge of index.outgoing.get(current.nodeId) || []) {
        if (edge.status !== 'resolved' || index.nodes.get(edge.target)?.kind === 'MAPPING_ISSUE') continue;
        const depth = current.nodeIds.length;
        if ((bestDepth.get(edge.target) ?? Infinity) < depth) continue;
        bestDepth.set(edge.target, depth);
        queue.push({ nodeId: edge.target, nodeIds: [...current.nodeIds, edge.target], edgeIds: [...current.edgeIds, edge.id] });
      }
    }
  }
  paths.sort((a, b) => {
    const selectedDelta = Number(b.nodeIds.includes(selectedId)) - Number(a.nodeIds.includes(selectedId));
    return selectedDelta || b.nodeIds.length - a.nodeIds.length;
  });
  return paths[0] || null;
}

export function buildGraphView(index, start, mode, lineage) {
  if (!start || !index.nodes.has(start)) return buildConnectionOverview(index);
  if (mode === 'lineage') {
    const nodeIds = new Set(lineage?.nodeIds || [start]);
    const edgeIds = new Set(lineage?.edgeIds || []);
    for (const nodeId of [...nodeIds]) {
      const incident = [...(index.incoming.get(nodeId) || []), ...(index.outgoing.get(nodeId) || [])];
      for (const edge of incident) {
        if (edge.status !== 'resolved') continue;
        const other = edge.source === nodeId ? edge.target : edge.source;
        if (index.nodes.get(other)?.kind === 'MAPPING_ISSUE') continue;
        nodeIds.add(other);
        edgeIds.add(edge.id);
      }
    }
    for (const edge of index.edges.values()) {
      if (edge.status === 'resolved' && nodeIds.has(edge.source) && nodeIds.has(edge.target)) edgeIds.add(edge.id);
    }
    return { nodeIds: [...nodeIds], edgeIds: [...edgeIds] };
  }

  const nodeIds = new Set([start]);
  const edgeIds = new Set();
  const queue = [start];
  while (queue.length && nodeIds.size < 300) {
    const current = queue.shift();
    const edges = mode === 'upstream' ? (index.incoming.get(current) || []) : (index.outgoing.get(current) || []);
    for (const edge of edges) {
      const next = mode === 'upstream' ? edge.source : edge.target;
      edgeIds.add(edge.id);
      if (!nodeIds.has(next)) {
        nodeIds.add(next);
        queue.push(next);
      }
    }
  }
  return { nodeIds: [...nodeIds], edgeIds: [...edgeIds] };
}

function buildConnectionOverview(index) {
  const edgeIds = [...index.edges.values()]
    .filter((edge) => edge.kind === 'device_connection_match' && edge.status === 'resolved')
    .map((edge) => edge.id);
  const nodeIds = new Set();
  for (const edgeId of edgeIds) {
    const edge = index.edges.get(edgeId);
    nodeIds.add(edge.source);
    nodeIds.add(edge.target);
  }
  for (const node of index.nodes.values()) {
    if (['SOURCE_DEVICE', 'IGNITION_DEVICE'].includes(node.kind)) nodeIds.add(node.id);
  }
  return { nodeIds: [...nodeIds].slice(0, 300), edgeIds };
}

export function buildFlowElements(index, graphView, selectedId, systemFilters, typeFilters) {
  const selectedKinds = TYPE_FILTERS.filter((group) => typeFilters.includes(group.label)).flatMap((group) => group.kinds);
  const visibleIds = new Set(graphView.nodeIds.filter((nodeId) => {
    const node = index.nodes.get(nodeId);
    if (!node) return false;
    if (nodeId === selectedId) return true;
    if (systemFilters.length && !systemFilters.includes(node.system)) return false;
    return !selectedKinds.length || selectedKinds.includes(node.kind);
  }));
  const controlEdges = graphView.edgeIds
    .map((edgeId) => index.edges.get(edgeId))
    .filter((edge) => edge && visibleIds.has(edge.source) && visibleIds.has(edge.target));
  const controlNodes = [...visibleIds].map((nodeId) => index.nodes.get(nodeId)).filter(Boolean);

  const layout = new dagre.graphlib.Graph({ multigraph: true })
    .setDefaultEdgeLabel(() => ({}))
    .setGraph({ rankdir: 'LR', ranksep: 82, nodesep: 42, edgesep: 22, marginx: 30, marginy: 30 });
  for (const node of controlNodes) layout.setNode(node.id, { width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT });
  for (const edge of controlEdges) layout.setEdge(edge.source, edge.target, {}, edge.id);
  dagre.layout(layout);

  const nodes = controlNodes.map((node) => {
    const position = layout.node(node.id) || { x: 0, y: 0 };
    return {
      id: node.id,
      type: 'controlNode',
      position: { x: position.x - FLOW_NODE_WIDTH / 2, y: position.y - FLOW_NODE_HEIGHT / 2 },
      data: { controlNode: node },
      selected: node.id === selectedId,
      draggable: false,
    };
  });
  const edges = controlEdges.map((edge) => {
    const unresolved = edge.status !== 'resolved';
    const identityMatch = ['communication_identity_match', 'device_connection_match'].includes(edge.kind);
    const color = unresolved
      ? darkTokens.semantic.warning
      : identityMatch ? darkTokens.semantic.success : darkTokens.graph.edge;
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
      style: { stroke: color, strokeWidth: identityMatch ? 2.4 : 1.7, strokeDasharray: unresolved ? '6 4' : undefined },
      animated: identityMatch,
      label: unresolved ? capitalize(edge.status) : undefined,
      labelStyle: { fill: darkTokens.semantic.warning, fontSize: 11, fontWeight: 700 },
      labelBgStyle: { fill: darkTokens.surface.overlay, fillOpacity: .96 },
      focusable: true,
    };
  });
  return { nodes, edges };
}

export function relationshipRows(index, selectedId, preferredIds) {
  const preferred = [...new Set(preferredIds)].map((id) => index.edges.get(id)).filter(Boolean);
  if (preferred.length) return preferred;
  return [...(index.incoming.get(selectedId) || []), ...(index.outgoing.get(selectedId) || [])];
}

export function countReachable(index, start, mode) {
  const seen = new Set([start]);
  const queue = [start];
  while (queue.length) {
    const current = queue.shift();
    const edges = mode === 'upstream' ? (index.incoming.get(current) || []) : (index.outgoing.get(current) || []);
    for (const edge of edges) {
      const next = mode === 'upstream' ? edge.source : edge.target;
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen.size - 1;
}

export function nodeStatus(index, nodeId) {
  const edges = [...(index.incoming.get(nodeId) || []), ...(index.outgoing.get(nodeId) || [])];
  return edges.some((edge) => edge.status !== 'resolved') || index.nodes.get(nodeId)?.kind === 'MAPPING_ISSUE'
    ? 'unresolved'
    : 'resolved';
}

export function sourceName(node) {
  const source = node.evidence?.[0]?.source;
  if (!source) return '—';
  const archivePart = source.split('!').pop();
  return archivePart.split(/[\\/]/).pop();
}

export function evidenceFiles(edge) {
  return [...new Set((edge.evidence || []).map((item) => {
    const archivePart = item.source.split('!').pop();
    return archivePart.split(/[\\/]/).pop();
  }).filter(Boolean))];
}

export function identityLabel(identity) {
  if (!identity || typeof identity !== 'object') return '—';
  const endpoint = identity.host || identity.server || identity.device;
  const point = [identity.object, identity.index].filter(Boolean).join(' ');
  return [String(identity.kind || '').toUpperCase(), endpoint, identity.unit && `unit ${identity.unit}`, point || identity.nodeid]
    .filter(Boolean).join(' · ');
}

export function shortName(name) {
  return name.replace(/^\[[^\]]+]\s*/, '').replace(/^_types_\//, '');
}

export function labelEdge(kind) {
  return kind.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function capitalize(value) {
  return String(value).replace(/^./, (letter) => letter.toUpperCase());
}

function connectedNodes(index, start) {
  const seen = new Set([start]);
  const queue = [start];
  while (queue.length) {
    const current = queue.shift();
    const edges = [...(index.outgoing.get(current) || []), ...(index.incoming.get(current) || [])];
    for (const edge of edges) {
      if (index.nodes.get(edge.source)?.kind === 'MAPPING_ISSUE' || index.nodes.get(edge.target)?.kind === 'MAPPING_ISSUE') continue;
      const next = edge.source === current ? edge.target : edge.source;
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen;
}
