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
import { FLOW_NODE_HEIGHT, FLOW_NODE_WIDTH, KIND_LABELS, SYSTEM_COLORS } from '../constants.js';
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
  const audit = model.summary.audit || {};
  const totalTags = audit.totalTagCount ?? model.summary.nodeKinds?.IGNITION_TAG ?? 0;
  const opcTags = audit.opcTagCount ?? model.summary.nodeKinds?.IGNITION_TAG ?? 0;
  const resolved = audit.resolvedTagCount ?? 0;
  const unresolved = audit.unresolvedTagCount ?? opcTags;
  return <Box sx={{ px: 2.5, pt: 1.75 }}>
    <Stack direction="row" spacing={1} alignItems="center">
      <Typography variant="h6" fontSize={18}>External Interface Audit</Typography>
      <Chip size="small" label="OPC tags only" color="primary" variant="outlined" />
    </Stack>
    <Paper variant="outlined" sx={{ display: 'flex', mt: 1.25, overflow: 'hidden', width: 'fit-content' }}>
      <Metric icon={<DeviceHubOutlined />} label="Total tags" value={totalTags.toLocaleString()} tone="text.secondary" />
      <Metric icon={<HubOutlined />} label="OPC audited" value={opcTags.toLocaleString()} tone="primary.light" />
      <Metric icon={<CheckCircleOutlineRounded />} label="Resolved" value={resolved.toLocaleString()} tone="success.main" />
      <Metric icon={<ErrorOutlineRounded />} label="Audit issues" value={unresolved.toLocaleString()} tone="warning.main" />
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
      <Alert severity="info" variant="outlined">
        {selectedId ? `No ${traceMode} path matches the active filters.` : 'Import data or select a connection device to inspect its lineage.'}
      </Alert>
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
        label={graphView.truncated
          ? `Canvas limited to ${graphView.nodeLimit} nodes`
          : !selectedId
            ? 'Connection overview'
            : traceMode === 'lineage' ? 'Complete lineage' : `${capitalize(traceMode)} trace`}
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
  const accent = SYSTEM_COLORS[node.system] || darkTokens.accent.gray;
  const handleColor = selected ? darkTokens.border.focused : darkTokens.graph.edge;
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
      borderColor: selected ? darkTokens.border.focused : darkTokens.border.bold,
      borderRadius: 1.25,
      boxShadow: selected
        ? `0 0 0 3px ${alpha(darkTokens.border.focused, .2)}, ${darkTokens.shadow.overlay}`
        : darkTokens.shadow.raised,
      position: 'relative',
      '&::before': {
        content: '""',
        position: 'absolute',
        left: 5,
        top: 12,
        bottom: 12,
        width: 2,
        borderRadius: 2,
        bgcolor: alpha(accent, selected ? .95 : .65),
      },
    }}
  >
    <Handle type="target" position={Position.Left} style={{ width: 7, height: 7, background: handleColor, borderColor: darkTokens.surface.sunken }} />
    <Typography variant="caption" sx={{ color: selected ? 'primary.light' : 'text.secondary', fontWeight: 700, letterSpacing: '.035em', textTransform: 'uppercase', lineHeight: 1.1 }}>
      {KIND_LABELS[node.kind] || node.kind}
    </Typography>
    <Tooltip
      title={node.name}
      placement="top"
      arrow
      enterDelay={300}
      slotProps={{ tooltip: { sx: { maxWidth: 560, overflowWrap: 'anywhere', fontSize: 12 } } }}
    >
      <Typography variant="body2" fontWeight={700} noWrap sx={{ mt: .55 }}>{shortName(node.name)}</Typography>
    </Tooltip>
    <Typography variant="caption" color="text.secondary" lineHeight={1.1}>
      {node.attributes?.usedSignalCount !== undefined
        ? `${node.attributes.usedSignalCount.toLocaleString()} used signals`
        : node.system}
    </Typography>
    <Handle type="source" position={Position.Right} style={{ width: 7, height: 7, background: handleColor, borderColor: darkTokens.surface.sunken }} />
  </Paper>;
}
