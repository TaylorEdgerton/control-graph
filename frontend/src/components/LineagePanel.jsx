import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { Alert, Box, Chip, IconButton, Paper, Stack, Tooltip, Typography } from '@mui/material';
import AddRounded from '@mui/icons-material/AddRounded';
import CenterFocusStrongRounded from '@mui/icons-material/CenterFocusStrongRounded';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import DeviceHubOutlined from '@mui/icons-material/DeviceHubOutlined';
import ErrorOutlineRounded from '@mui/icons-material/ErrorOutlineRounded';
import FullscreenRounded from '@mui/icons-material/FullscreenRounded';
import HubOutlined from '@mui/icons-material/HubOutlined';
import RemoveRounded from '@mui/icons-material/RemoveRounded';
import {
  Background,
  BackgroundVariant,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from '@xyflow/react';
import { alpha } from '@mui/material/styles';
import { FLOW_NODE_HEIGHT, FLOW_NODE_WIDTH, KIND_COLORS, KIND_LABELS } from '../constants.js';
import { buildFlowElements, capitalize, shortName } from '../graph-utils.js';
import { darkTokens } from '../theme-tokens.js';

const NODE_TYPES = { controlNode: ControlFlowNode };

export default function LineagePanel({
  model, index, selectedId, graphView, traceMode, systemFilters, typeFilters, selectNode, inspectEdge,
}) {
  const panelRef = useRef(null);

  async function showFullscreen() {
    if (panelRef.current?.requestFullscreen) await panelRef.current.requestFullscreen();
  }

  return <Box ref={panelRef} sx={{
    gridColumn: { lg: 2 }, gridRow: { lg: 1 }, bgcolor: 'background.paper', borderRight: '1px solid',
    borderBottom: '1px solid', borderColor: 'divider', minWidth: 0, display: 'flex', flexDirection: 'column',
  }}>
    <GraphHeader model={model} />
    <Box sx={{ flex: 1, minHeight: 0, position: 'relative', bgcolor: 'controlGraph.surface.sunken' }}>
      <ReactFlowProvider>
        <FlowScene
          index={index}
          selectedId={selectedId}
          graphView={graphView}
          traceMode={traceMode}
          systemFilters={systemFilters}
          typeFilters={typeFilters}
          selectNode={selectNode}
          inspectEdge={inspectEdge}
          fullscreen={showFullscreen}
        />
      </ReactFlowProvider>
    </Box>
  </Box>;
}

function GraphHeader({ model }) {
  const resolved = model.edges.filter((edge) => edge.status === 'resolved').length;
  const unresolved = model.edges.length - resolved;
  const resolvedRate = model.edges.length ? Math.round((resolved / model.edges.length) * 100) : 0;
  return <Box sx={{ px: 2.5, pt: 1.75 }}>
    <Typography variant="h6" fontSize={18}>Signal Lineage Graph</Typography>
    <Paper variant="outlined" sx={{ display: 'flex', mt: 1.25, overflow: 'hidden', width: 'fit-content' }}>
      <Metric icon={<DeviceHubOutlined />} label="Nodes" value={model.summary.nodeCount} tone="primary.light" />
      <Metric icon={<HubOutlined />} label="Edges" value={model.summary.edgeCount} tone="secondary.light" />
      <Metric icon={<CheckCircleOutlineRounded />} label="Resolved" value={`${resolvedRate}%`} tone="success.main" />
      <Metric icon={<ErrorOutlineRounded />} label="Unresolved" value={unresolved} tone="warning.main" />
    </Paper>
  </Box>;
}

function Metric({ icon, label, value, tone }) {
  return <Stack direction="row" alignItems="center" spacing={1} sx={{ px: { xs: 1, xl: 2 }, py: 1, borderRight: '1px solid', borderColor: 'divider', minWidth: { xs: 76, xl: 112 } }}>
    <Box sx={{ color: tone, display: { xs: 'none', xl: 'block' } }}>{icon}</Box>
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{label}</Typography>
      <Typography variant="subtitle2" lineHeight={1.1}>{value}</Typography>
    </Box>
  </Stack>;
}

function FlowScene({
  index, selectedId, graphView, traceMode, systemFilters, typeFilters,
  selectNode, inspectEdge, fullscreen,
}) {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const flow = useMemo(
    () => buildFlowElements(index, graphView, selectedId, systemFilters, typeFilters),
    [index, graphView, selectedId, systemFilters, typeFilters],
  );
  const fit = useCallback(() => fitView({ padding: .24, duration: 250 }), [fitView]);

  useEffect(() => {
    const frame = requestAnimationFrame(fit);
    return () => cancelAnimationFrame(frame);
  }, [fit, flow.nodes.length, flow.edges.length]);

  if (!flow.nodes.length) {
    return <Box sx={{ height: '100%', display: 'grid', placeItems: 'center', p: 3 }}>
      <Alert severity="info" variant="outlined">No {traceMode} path matches the active filters.</Alert>
    </Box>;
  }

  return <ReactFlow
    nodes={flow.nodes}
    edges={flow.edges}
    nodeTypes={NODE_TYPES}
    colorMode="dark"
    fitView
    fitViewOptions={{ padding: .24 }}
    minZoom={.25}
    maxZoom={1.8}
    nodesDraggable={false}
    nodesConnectable={false}
    edgesReconnectable={false}
    elementsSelectable
    selectionOnDrag={false}
    panOnDrag
    zoomOnScroll
    onNodeClick={(_, flowNode) => selectNode(flowNode.data.controlNode)}
    onEdgeClick={(_, flowEdge) => inspectEdge(index.edges.get(flowEdge.id))}
    defaultEdgeOptions={{ type: 'smoothstep' }}
    proOptions={{ hideAttribution: true }}
  >
    <Background variant={BackgroundVariant.Dots} gap={22} size={1} color={darkTokens.graph.grid} />
    <Panel position="top-left">
      <Chip
        size="small"
        label={traceMode === 'lineage' ? 'Complete lineage' : `${capitalize(traceMode)} trace`}
        sx={{ bgcolor: 'controlGraph.surface.overlay', border: '1px solid', borderColor: 'divider' }}
      />
    </Panel>
    <Panel position="top-right">
      <Stack direction="row" spacing={.5} sx={{ p: .5, bgcolor: 'controlGraph.surface.overlay', border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
        <CanvasButton title="Fit graph" onClick={fit}><CenterFocusStrongRounded fontSize="small" /></CanvasButton>
        <CanvasButton title="Zoom in" onClick={() => zoomIn({ duration: 150 })}><AddRounded fontSize="small" /></CanvasButton>
        <CanvasButton title="Zoom out" onClick={() => zoomOut({ duration: 150 })}><RemoveRounded fontSize="small" /></CanvasButton>
        <CanvasButton title="Show fullscreen" onClick={fullscreen}><FullscreenRounded fontSize="small" /></CanvasButton>
      </Stack>
    </Panel>
  </ReactFlow>;
}

function CanvasButton({ title, onClick, children }) {
  return <Tooltip title={title} placement="bottom"><IconButton size="small" onClick={onClick} sx={{ borderRadius: .75 }}>{children}</IconButton></Tooltip>;
}

function ControlFlowNode({ data, selected }) {
  const node = data.controlNode;
  const color = KIND_COLORS[node.kind] || darkTokens.accent.gray;
  return <Paper
    elevation={selected ? 8 : 1}
    sx={{
      width: FLOW_NODE_WIDTH,
      height: FLOW_NODE_HEIGHT,
      px: 1.4,
      py: 1.1,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      bgcolor: selected ? darkTokens.background.selected : darkTokens.surface.raised,
      border: selected ? '2px solid' : '1px solid',
      borderColor: selected ? color : darkTokens.border.bold,
      borderLeft: `4px solid ${color}`,
      borderRadius: 1.25,
      boxShadow: selected
        ? `0 0 0 3px ${alpha(color, .22)}, ${darkTokens.shadow.overlay}`
        : darkTokens.shadow.raised,
    }}
  >
    <Handle type="target" position={Position.Left} style={{ width: 7, height: 7, background: color, borderColor: darkTokens.surface.sunken }} />
    <Typography variant="caption" sx={{ color, fontWeight: 700, letterSpacing: '.035em', textTransform: 'uppercase', lineHeight: 1.1 }}>
      {KIND_LABELS[node.kind] || node.kind}
    </Typography>
    <Typography variant="body2" fontWeight={700} noWrap title={node.name} sx={{ mt: .55 }}>{shortName(node.name)}</Typography>
    <Typography variant="caption" color="text.secondary" lineHeight={1.1}>{node.system}</Typography>
    <Handle type="source" position={Position.Right} style={{ width: 7, height: 7, background: color, borderColor: darkTokens.surface.sunken }} />
  </Paper>;
}
