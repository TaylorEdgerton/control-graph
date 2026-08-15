import React, { useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  FormControlLabel,
  FormGroup,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import FilterAltOffRounded from '@mui/icons-material/FilterAltOffRounded';
import SearchRounded from '@mui/icons-material/SearchRounded';
import { RichTreeView } from '@mui/x-tree-view/RichTreeView';
import { SYSTEM_FILTERS, TYPE_FILTERS } from '../constants.js';
import { collectBranchIds } from '../graph-utils.js';

export default function ControlBrowser({
  query, setQuery, systemFilters, typeFilters, toggleSystem, toggleType,
  clearFilters, hasActiveFilters, treeItems, resultCount, nodeIndex, selectedId, selectNode,
}) {
  const [expandedItems, setExpandedItems] = useState(() => treeItems.map((item) => item.id));
  const visibleExpandedItems = query ? collectBranchIds(treeItems) : expandedItems;

  function selectTreeItem(_, itemId) {
    if (!itemId?.startsWith('node:')) return;
    const node = nodeIndex.get(itemId.slice(5));
    if (node) selectNode(node);
  }

  return <Paper square elevation={0} sx={{
    gridColumn: { lg: 1 }, gridRow: { lg: '1 / 3' }, borderRight: '1px solid', borderColor: 'divider',
    minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden',
  }}>
    <Box sx={{ p: 2.25, pb: 1.5 }}>
      <Typography variant="h6" fontSize={18} sx={{ mb: 1.5 }}>Search &amp; Filters</Typography>
      <TextField
        fullWidth size="small" value={query} onChange={(event) => setQuery(event.target.value)}
        placeholder="Search signal, tag, device…"
        slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchRounded fontSize="small" /></InputAdornment> } }}
      />
    </Box>
    <Box sx={{ px: 2.25, overflowY: 'auto', flex: 1 }}>
      <FilterSection title="Systems">
        <FormGroup>
          {SYSTEM_FILTERS.map((filter) => <FormControlLabel
            key={filter.value}
            control={<Checkbox size="small" checked={systemFilters.includes(filter.value)} onChange={() => toggleSystem(filter.value)} />}
            label={filter.label}
          />)}
        </FormGroup>
      </FilterSection>
      <FilterSection title="Node Types">
        <FormGroup>
          {TYPE_FILTERS.map((filter) => <FormControlLabel
            key={filter.label}
            control={<Checkbox size="small" checked={typeFilters.includes(filter.label)} onChange={() => toggleType(filter.label)} />}
            label={filter.label}
          />)}
        </FormGroup>
      </FilterSection>
      <Divider sx={{ mt: 1.25 }} />
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ py: 1.25 }}>
        <Typography variant="subtitle2">Control Browser</Typography>
        <Chip size="small" label={resultCount} />
      </Stack>
      <RichTreeView
        items={treeItems}
        itemHeight={30}
        expandedItems={visibleExpandedItems}
        onExpandedItemsChange={(_, itemIds) => setExpandedItems(itemIds)}
        selectedItems={selectedId ? `node:${selectedId}` : null}
        onSelectedItemsChange={selectTreeItem}
        expansionTrigger="iconContainer"
        sx={{
          minWidth: 0,
          pb: 2,
          '& .MuiTreeItem-label': { fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
          '& .MuiTreeItem-content': { borderRadius: 1 },
          '& .MuiTreeItem-content:hover': { bgcolor: 'controlGraph.background.neutral' },
          '& .MuiTreeItem-content.Mui-selected': {
            bgcolor: 'controlGraph.background.selected',
            color: 'primary.light',
          },
          '& .MuiTreeItem-content.Mui-selected:hover': { bgcolor: 'controlGraph.background.selectedHovered' },
          '& .MuiTreeItem-iconContainer': { color: 'text.secondary' },
        }}
      />
    </Box>
    <Box sx={{ p: 2 }}>
      <Button fullWidth variant="outlined" color="inherit" startIcon={<FilterAltOffRounded />} onClick={clearFilters} disabled={!hasActiveFilters}>
        Clear Filters
      </Button>
    </Box>
  </Paper>;
}

function FilterSection({ title, children }) {
  return <Box sx={{ py: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
    <Typography variant="subtitle2" sx={{ mb: .5 }}>{title}</Typography>
    {children}
  </Box>;
}
