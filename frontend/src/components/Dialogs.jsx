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
  Typography,
} from '@mui/material';
import ArrowForwardRounded from '@mui/icons-material/ArrowForwardRounded';
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
        <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
          <Typography component="pre" variant="caption" sx={{ m: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(edge.attributes, null, 2)}</Typography>
        </Paper>
      </>}
      <Typography variant="subtitle2" sx={{ mb: .75 }}>Source provenance</Typography>
      <Stack spacing={1}>
        {(edge.evidence || []).map((evidence, position) => <Paper key={`${evidence.source}-${evidence.location}-${position}`} variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="body2" fontWeight={700} sx={{ overflowWrap: 'anywhere' }}>{evidence.source}</Typography>
          <Typography variant="caption" color="primary" sx={{ display: 'block', overflowWrap: 'anywhere' }}>{evidence.location}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: .75, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{evidence.detail}</Typography>
        </Paper>)}
      </Stack>
    </DialogContent>}
    <DialogActions><Button onClick={close}>Close</Button></DialogActions>
  </Dialog>;
}
