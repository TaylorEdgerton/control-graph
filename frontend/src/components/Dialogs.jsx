import React from 'react';
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import ArrowForwardRounded from '@mui/icons-material/ArrowForwardRounded';
import { flattenKeyValuePairs, formatAttributeKey } from '../display-utils.js';
import { labelEdge } from '../graph-utils.js';

export function EvidenceDialog({ edge, index, close }) {
  const from = edge ? index.nodes.get(edge.source) : null;
  const to = edge ? index.nodes.get(edge.target) : null;
  return <Dialog open={Boolean(edge)} onClose={close} fullWidth maxWidth="md">
    <DialogTitle>Relationship Evidence</DialogTitle>
    {edge && <DialogContent dividers>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }} sx={{ mb: 2 }}>
        <Chip label={from?.name || edge.source} />
        <ArrowForwardRounded color="action" />
        <Chip label={labelEdge(edge.kind)} color="primary" variant="outlined" />
        <ArrowForwardRounded color="action" />
        <Chip label={to?.name || edge.target} />
      </Stack>
      {Object.keys(edge.attributes || {}).length > 0 && <>
        <Typography variant="subtitle2" sx={{ mb: .75 }}>Resolver data</Typography>
        <KeyValueTable values={edge.attributes} />
      </>}
      <Typography variant="subtitle2" sx={{ mb: .75 }}>Source provenance</Typography>
      <Stack spacing={1}>
        {(edge.evidence || []).map((evidence, position) => <Paper key={`${evidence.source}-${evidence.location}-${position}`} variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="body2" fontWeight={700} sx={{ overflowWrap: 'anywhere' }}>{evidence.source}</Typography>
          <Typography variant="caption" color="primary" sx={{ display: 'block', overflowWrap: 'anywhere' }}>{evidence.location}</Typography>
          <EvidenceDetail detail={evidence.detail} />
        </Paper>)}
      </Stack>
    </DialogContent>}
    <DialogActions><Button onClick={close}>Close</Button></DialogActions>
  </Dialog>;
}

function EvidenceDetail({ detail }) {
  let parsed;
  try {
    parsed = JSON.parse(detail);
  } catch {
    parsed = null;
  }
  if (parsed && typeof parsed === 'object') {
    return <KeyValueTable values={parsed} compact />;
  }
  return <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: .75, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
    {detail}
  </Typography>;
}

function KeyValueTable({ values, compact = false }) {
  const rows = flattenKeyValuePairs(values);
  return <TableContainer component={Paper} variant="outlined" sx={{ mt: compact ? 1 : 0, mb: compact ? 0 : 2, maxHeight: 320 }}>
    <Table size="small" stickyHeader aria-label="Relationship resolver details">
      <TableHead>
        <TableRow>
          <TableCell sx={{ width: '42%' }}>Property</TableCell>
          <TableCell>Value</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => <TableRow key={row.key}>
          <TableCell component="th" scope="row" sx={{ color: 'text.secondary', verticalAlign: 'top' }}>
            {formatAttributeKey(row.key)}
          </TableCell>
          <TableCell sx={{ overflowWrap: 'anywhere', verticalAlign: 'top' }}>{row.value}</TableCell>
        </TableRow>)}
      </TableBody>
    </Table>
  </TableContainer>;
}
