import React from 'react';
import { AppBar, Box, Button, Stack, Toolbar, Typography } from '@mui/material';
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined';
import CloudUploadOutlined from '@mui/icons-material/CloudUploadOutlined';
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined';
import PlayArrowRounded from '@mui/icons-material/PlayArrowRounded';

export default function AppHeader({ onImport, onValidate, onExport }) {
  return <AppBar position="static" color="inherit" elevation={0} sx={{ borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'controlGraph.surface.raised' }}>
    <Toolbar sx={{ minHeight: { xs: 64, md: 72 }, gap: 1.5 }}>
      <Box sx={{
        width: 40, height: 40, border: '1px solid', borderColor: 'primary.light', color: 'primary.light',
        bgcolor: 'controlGraph.background.selected', display: 'grid', placeItems: 'center',
      }}>
        <AccountTreeOutlined />
      </Box>
      <Typography variant="h5" sx={{ flexGrow: 1 }}>ControlGraph</Typography>
      <Stack direction="row" spacing={1.25}>
        <HeaderButton icon={<CloudUploadOutlined />} label="Import Project" onClick={onImport} />
        <HeaderButton icon={<PlayArrowRounded />} label="Run Validation" onClick={onValidate} primary />
        <HeaderButton icon={<DescriptionOutlined />} label="Export Report" onClick={onExport} />
      </Stack>
    </Toolbar>
  </AppBar>;
}

function HeaderButton({ icon, label, onClick, primary = false }) {
  return <Button
    variant={primary ? 'contained' : 'outlined'}
    color={primary ? 'primary' : 'inherit'}
    startIcon={icon}
    onClick={onClick}
    sx={{ height: 42, px: { xs: 1, md: 2 } }}
  >
    <Box component="span" sx={{ display: { xs: 'none', md: 'inline' } }}>{label}</Box>
  </Button>;
}
