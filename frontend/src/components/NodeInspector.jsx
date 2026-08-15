import React from 'react';
import { Alert, Box, Button, Chip, Paper, Stack, Typography } from '@mui/material';
import ArrowDownwardRounded from '@mui/icons-material/ArrowDownwardRounded';
import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded';
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded';
import ErrorOutlineRounded from '@mui/icons-material/ErrorOutlineRounded';
import { KIND_LABELS, SYSTEM_COLORS } from '../constants.js';
import { capitalize, identityLabel, shortName, sourceName } from '../graph-utils.js';

export default function NodeInspector({ node, status, upstreamCount, downstreamCount, traceMode, showTrace }) {
  const systemColor = node ? SYSTEM_COLORS[node.system] : null;
  return <Paper square elevation={0} sx={{
    gridColumn: { lg: 3 }, gridRow: { lg: 1 }, borderBottom: '1px solid', borderColor: 'divider',
    minHeight: 0, overflowY: 'auto', p: 2.25,
  }}>
    <Typography variant="h6" fontSize={18}>Selected Node</Typography>
    {!node ? <Alert severity="info" sx={{ mt: 2 }}>Select a node in the graph.</Alert> : <>
      <Paper variant="outlined" sx={{
        p: 1.5,
        mt: 1.5,
        textAlign: 'center',
        bgcolor: 'controlGraph.background.neutral',
        borderLeft: `2px solid ${systemColor}`,
      }}>
        <Typography variant="body1" fontWeight={700} sx={{ overflowWrap: 'anywhere' }}>{KIND_LABELS[node.kind]}: {shortName(node.name)}</Typography>
      </Paper>
      <Box component="dl" sx={{ m: 0, mt: 1.5 }}>
        <InspectorRow label="Type" value={KIND_LABELS[node.kind] || node.kind} />
        <InspectorRow label="System" value={node.system} />
        <InspectorRow label="Source file" value={sourceName(node)} />
        <InspectorRow label="Upstream count" value={upstreamCount} />
        <InspectorRow label="Downstream count" value={downstreamCount} />
        <InspectorRow label="Protocol mapping" value={identityLabel(node.attributes?.identity)} />
        <InspectorRow label="Status" value={<Chip
          size="small"
          icon={status === 'resolved' ? <CheckCircleOutlineRounded /> : <ErrorOutlineRounded />}
          label={capitalize(status)} color={status === 'resolved' ? 'success' : 'warning'}
        />} />
      </Box>
      <Stack spacing={1.25} sx={{ mt: 2.25 }}>
        <Button
          fullWidth variant={traceMode === 'upstream' ? 'contained' : 'outlined'}
          color={traceMode === 'upstream' ? 'primary' : 'inherit'}
          startIcon={<ArrowUpwardRounded />} onClick={() => showTrace('upstream')}
        >Trace Upstream</Button>
        <Button
          fullWidth variant={traceMode === 'downstream' ? 'contained' : 'outlined'}
          color={traceMode === 'downstream' ? 'primary' : 'inherit'}
          startIcon={<ArrowDownwardRounded />} onClick={() => showTrace('downstream')}
        >Trace Downstream</Button>
        {traceMode !== 'lineage' && <Button fullWidth onClick={() => showTrace('lineage')}>Show Complete Lineage</Button>}
      </Stack>
    </>}
  </Paper>;
}

function InspectorRow({ label, value }) {
  return <Box sx={{ display: 'grid', gridTemplateColumns: 'minmax(110px, 44%) 1fr', gap: 1, alignItems: 'center', py: 1.2, borderBottom: '1px solid', borderColor: 'divider' }}>
    <Typography component="dt" variant="body2" fontWeight={700}>{label}:</Typography>
    <Typography component="dd" variant="body2" sx={{ m: 0, overflowWrap: 'anywhere' }}>{value || '—'}</Typography>
  </Box>;
}
