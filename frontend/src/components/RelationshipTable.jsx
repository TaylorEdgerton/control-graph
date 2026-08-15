import React from 'react';
import {
  Chip,
  Divider,
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
import { capitalize, evidenceFiles, labelEdge } from '../graph-utils.js';

export default function RelationshipTable({ index, relationships, selectedEdge, inspectEdge }) {
  return <Paper square elevation={0} sx={{ gridColumn: { lg: '2 / 4' }, gridRow: { lg: 2 }, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 2.5, py: 1.25 }}>
      <Typography variant="h6" fontSize={18}>Evidence / Relationships</Typography>
      <Typography variant="caption" color="text.secondary">Select a row to see its source evidence</Typography>
    </Stack>
    <Divider />
    <TableContainer sx={{ flex: 1 }}>
      <Table stickyHeader size="small" aria-label="Relationship evidence">
        <TableHead>
          <TableRow>
            <TableCell>From</TableCell>
            <TableCell>Relationship</TableCell>
            <TableCell>To</TableCell>
            <TableCell>Evidence</TableCell>
            <TableCell>Status</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {relationships.map((edge) => {
            const from = index.nodes.get(edge.source);
            const to = index.nodes.get(edge.target);
            return <TableRow
              hover key={edge.id} selected={selectedEdge?.id === edge.id} onClick={() => inspectEdge(edge)}
              sx={{ cursor: 'pointer' }}
            >
              <TableCell>{from?.name || edge.source}</TableCell>
              <TableCell><Typography variant="caption" fontWeight={700}>{labelEdge(edge.kind).toUpperCase()}</Typography></TableCell>
              <TableCell>{to?.name || edge.target}</TableCell>
              <TableCell>{evidenceFiles(edge).join(', ') || 'Resolver'}</TableCell>
              <TableCell><Chip size="small" label={capitalize(edge.status)} color={edge.status === 'resolved' ? 'success' : 'warning'} /></TableCell>
            </TableRow>;
          })}
          {!relationships.length && <TableRow><TableCell colSpan={5}><Typography color="text.secondary">No relationships are available.</Typography></TableCell></TableRow>}
        </TableBody>
      </Table>
    </TableContainer>
  </Paper>;
}
