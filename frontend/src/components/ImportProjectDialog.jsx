import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormHelperText,
  IconButton,
  InputLabel,
  LinearProgress,
  ListItemText,
  MenuItem,
  OutlinedInput,
  Paper,
  Select,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import CloudUploadOutlined from '@mui/icons-material/CloudUploadOutlined';
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined';
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded';
import FolderZipOutlined from '@mui/icons-material/FolderZipOutlined';

export default function ImportProjectDialog({ open, close, onGraphChange, notify }) {
  const [staged, setStaged] = useState([]);
  const [imports, setImports] = useState([]);
  const [selectedProviders, setSelectedProviders] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return undefined;
    let active = true;
    setError('');
    requestJson('/api/imports')
      .then((data) => {
        if (!active) return;
        setStaged(data.staged);
        setImports(data.imports);
        setSelectedProviders(providerSelections(data.staged));
      })
      .catch((requestError) => active && setError(requestError.message));
    return () => { active = false; };
  }, [open]);

  async function selectFiles(event) {
    const files = [...event.target.files];
    event.target.value = '';
    if (!files.length) return;
    setBusy(true);
    setError('');
    try {
      for (const file of files) {
        const data = await requestJson('/api/imports/stage', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-ControlGraph-Filename': encodeURIComponent(file.name),
          },
          body: file,
        });
        setStaged((current) => [...current, ...data.staged]);
        setSelectedProviders((current) => ({ ...current, ...providerSelections(data.staged) }));
      }
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function discardStage(recordId) {
    setBusy(true);
    setError('');
    try {
      const data = await requestJson(`/api/imports/staged/${recordId}`, { method: 'DELETE' });
      setStaged(data.staged);
      setSelectedProviders((current) => {
        const next = { ...current };
        delete next[recordId];
        return next;
      });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmImport() {
    setBusy(true);
    setError('');
    try {
      const data = await requestJson('/api/imports/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selections: staged.map((item) => ({
            stagedId: item.id,
            tagProviders: selectedProviders[item.id] || [],
          })),
        }),
      });
      setStaged([]);
      setSelectedProviders({});
      setImports(data.imports);
      onGraphChange(data.graph);
      notify(`${data.imports.length} project ${data.imports.length === 1 ? 'file is' : 'files are'} in the analysis.`);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeImport(recordId) {
    setBusy(true);
    setError('');
    try {
      const data = await requestJson(`/api/imports/${recordId}`, { method: 'DELETE' });
      setImports(data.imports);
      onGraphChange(data.graph);
      notify('The project file was removed from the analysis.');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <Dialog open={open} onClose={busy ? undefined : close} fullWidth maxWidth="md">
    <DialogTitle>Import Projects</DialogTitle>
    <DialogContent dividers>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }} justifyContent="space-between">
        <Box>
          <Typography variant="subtitle2">Select Gateway backups or control-device XML projects</Typography>
          <Typography variant="caption" color="text.secondary">Files are detected and reviewed before they enter the analysis.</Typography>
        </Box>
        <Button component="label" variant="outlined" startIcon={<CloudUploadOutlined />} disabled={busy}>
          Select Files
          <input hidden type="file" accept=".gwbk,.xml,application/zip,application/xml,text/xml" multiple onChange={selectFiles} />
        </Button>
      </Stack>

      {busy && <LinearProgress sx={{ mt: 2 }} />}
      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}

      <SectionTitle title="Ready to import" count={staged.length} sx={{ mt: 3 }} />
      <Stack spacing={1.25} sx={{ mt: 1 }}>
        {staged.map((project) => <ProjectCard
          key={project.id}
          project={project}
          selectedProviders={selectedProviders[project.id] || []}
          onProvidersChange={(providers) => setSelectedProviders((current) => ({
            ...current,
            [project.id]: providers,
          }))}
          action={<Tooltip title="Discard this project"><span><IconButton
            aria-label={`Discard ${project.name}`}
            onClick={() => discardStage(project.id)}
            disabled={busy}
          ><DeleteOutlineRounded /></IconButton></span></Tooltip>}
        />)}
        {!staged.length && <EmptyState>No project files are waiting for confirmation.</EmptyState>}
      </Stack>

      <Divider sx={{ my: 3 }} />
      <SectionTitle title="Included in analysis" count={imports.length} />
      <Stack spacing={1.25} sx={{ mt: 1 }}>
        {imports.map((project) => <ProjectCard
          key={project.id}
          project={project}
          imported
          action={<Tooltip title="Remove from analysis"><span><IconButton
            aria-label={`Remove ${project.name} from analysis`}
            onClick={() => removeImport(project.id)}
            disabled={busy}
          ><DeleteOutlineRounded /></IconButton></span></Tooltip>}
        />)}
        {!imports.length && <EmptyState>No uploaded backups are included in the analysis.</EmptyState>}
      </Stack>
    </DialogContent>
    <DialogActions>
      <Button onClick={close} color="inherit" disabled={busy}>Close</Button>
      <Button
        variant="contained"
        onClick={confirmImport}
        disabled={busy || !staged.length || staged.some((item) => (
          item.importKind === 'ignition' && !selectedProviders[item.id]?.length
        ))}
        startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <CloudUploadOutlined />}
      >
        Import {staged.length || ''} {staged.length === 1 ? 'Project' : 'Projects'}
      </Button>
    </DialogActions>
  </Dialog>;
}

function SectionTitle({ title, count, sx }) {
  return <Stack direction="row" alignItems="center" spacing={1} sx={sx}>
    <Typography variant="subtitle2">{title}</Typography>
    <Chip size="small" label={count} />
  </Stack>;
}

function ProjectCard({
  project,
  imported = false,
  action,
  selectedProviders = [],
  onProvidersChange,
}) {
  const importedProviders = project.selectedTagProviders || [];
  const isGateway = project.importKind === 'ignition';
  return <Paper variant="outlined" sx={{ p: 1.5 }}>
    <Stack direction="row" spacing={1.5} alignItems="flex-start">
      <Box sx={{ color: imported ? 'success.main' : 'primary.light', pt: .25 }}>
        {isGateway ? <FolderZipOutlined /> : <AccountTreeOutlined />}
      </Box>
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography variant="body2" fontWeight={700} sx={{ overflowWrap: 'anywhere' }}>{project.name}</Typography>
        <Stack direction="row" spacing={.75} useFlexGap flexWrap="wrap" sx={{ mt: .75 }}>
          <Chip size="small" label={project.fileType} />
          {isGateway && <Chip size="small" color="primary" variant="outlined" label={`Ignition ${project.versionFamily}`} />}
          {!isGateway && project.projectName && <Chip size="small" color="primary" variant="outlined" label={project.projectName} />}
          <Chip size="small" label={project.configurationFormat.toUpperCase()} />
          <Chip size="small" label={formatBytes(project.size)} />
        </Stack>
        {isGateway ? <>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Version {project.version} · {project.configurationSource} · {project.tagConfigurationCount.toLocaleString()} tag configurations
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {project.backupType || 'Gateway'} backup{project.timestamp ? ` · ${project.timestamp}` : ''}
            {imported ? ` · ${project.nodeCount.toLocaleString()} nodes · ${project.deviceCount.toLocaleString()} devices` : ''}
          </Typography>
        </> : <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Root {project.rootElement} · {project.sourceNodeCount.toLocaleString()} recognized nodes · {project.sourceDeviceCount.toLocaleString()} devices · {project.protocolPointCount.toLocaleString()} protocol points
        </Typography>}
        {imported && isGateway && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {(project.opcTagCount || 0).toLocaleString()} OPC tags audited · {(project.excludedTagCount || 0).toLocaleString()} non-OPC tags excluded
          {(project.invalidOpcPathCount || project.missingConnectionCount)
            ? ` · ${(project.invalidOpcPathCount || 0).toLocaleString()} invalid paths · ${(project.missingConnectionCount || 0).toLocaleString()} missing connections`
            : ''}
        </Typography>}
        {imported && isGateway && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: .5 }}>
          Tag providers: {importedProviders.join(', ') || 'None'}
        </Typography>}
        {!imported && isGateway && <FormControl fullWidth size="small" sx={{ mt: 1.5 }}>
          <InputLabel id={`${project.id}-providers-label`}>Tag providers</InputLabel>
          <Select
            labelId={`${project.id}-providers-label`}
            multiple
            value={selectedProviders}
            onChange={(event) => onProvidersChange(event.target.value)}
            input={<OutlinedInput label="Tag providers" />}
            renderValue={(selected) => selected.join(', ')}
          >
            {project.tagProviders.map((provider) => <MenuItem key={provider} value={provider}>
              <Checkbox checked={selectedProviders.includes(provider)} />
              <ListItemText primary={provider} />
            </MenuItem>)}
          </Select>
          <FormHelperText>Select the gateway tag providers to include in the lineage analysis.</FormHelperText>
        </FormControl>}
      </Box>
      {action}
    </Stack>
  </Paper>;
}

function providerSelections(backups) {
  return Object.fromEntries(backups.map((project) => [
    project.id,
    project.importKind === 'ignition'
      ? (project.selectedTagProviders?.length ? project.selectedTagProviders : project.tagProviders)
      : [],
  ]));
}

function EmptyState({ children }) {
  return <Paper variant="outlined" sx={{ p: 2, bgcolor: 'controlGraph.background.neutral' }}>
    <Typography variant="body2" color="text.secondary">{children}</Typography>
  </Paper>;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) throw new Error(data?.detail || `The request failed with status ${response.status}.`);
  return data;
}
