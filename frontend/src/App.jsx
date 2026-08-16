import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Box, CircularProgress, Snackbar, Stack, Typography } from '@mui/material';
import AppHeader from './components/AppHeader.jsx';
import ControlBrowser from './components/ControlBrowser.jsx';
import { EvidenceDialog } from './components/Dialogs.jsx';
import ImportProjectDialog from './components/ImportProjectDialog.jsx';
import LineagePanel from './components/LineagePanel.jsx';
import NodeInspector from './components/NodeInspector.jsx';
import RelationshipTable from './components/RelationshipTable.jsx';
import {
  buildBrowserTree,
  buildGraphView,
  buildIndex,
  countReachable,
  findLineage,
  nodeStatus,
  relationshipRows,
  relationshipTotal,
  searchNodes,
} from './graph-utils.js';

export default function App() {
  const [model, setModel] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [systemFilters, setSystemFilters] = useState([]);
  const [typeFilters, setTypeFilters] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [traceMode, setTraceMode] = useState('lineage');
  const [evidenceEdge, setEvidenceEdge] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    fetch('/api/graph')
      .then((response) => {
        if (!response.ok) throw new Error(`The API returned status ${response.status}.`);
        return response.json();
      })
      .then((data) => {
        setModel(data);
        const first = data.nodes.find((node) => node.name === 'Relay_A')
          || data.nodes.find((node) => node.kind === 'SOURCE_DEVICE')
          || data.nodes[0];
        setSelectedId(first?.id || null);
      })
      .catch((error) => setLoadError(error.message));
  }, []);

  const index = useMemo(() => buildIndex(model), [model]);
  const selected = index.nodes.get(selectedId);
  const browserNodes = useMemo(
    () => searchNodes(model?.nodes || [], query, systemFilters, typeFilters),
    [model, query, systemFilters, typeFilters],
  );
  const browserTree = useMemo(() => buildBrowserTree(browserNodes), [browserNodes]);
  const lineage = useMemo(() => findLineage(index, selectedId), [index, selectedId]);
  const graphView = useMemo(
    () => buildGraphView(index, selectedId, traceMode, lineage),
    [index, selectedId, traceMode, lineage],
  );
  const relationships = useMemo(
    () => relationshipRows(index, selectedId, graphView.edgeIds),
    [index, selectedId, graphView],
  );
  const totalRelationships = useMemo(
    () => relationshipTotal(index, selectedId, graphView.edgeIds),
    [index, selectedId, graphView],
  );

  if (loadError) {
    return <Box sx={{ p: 4 }}><Alert severity="error">ControlGraph cannot load the model. {loadError}</Alert></Box>;
  }
  if (!model) {
    return <Stack alignItems="center" justifyContent="center" sx={{ minHeight: '100vh' }} spacing={2}>
      <CircularProgress size={30} />
      <Typography color="text.secondary">Load the control model…</Typography>
    </Stack>;
  }

  const hasActiveFilters = Boolean(query || systemFilters.length || typeFilters.length);
  const selectedStatus = selected ? nodeStatus(index, selected.id) : 'unresolved';
  const upstreamCount = selected ? countReachable(index, selected.id, 'upstream') : 0;
  const downstreamCount = selected ? countReachable(index, selected.id, 'downstream') : 0;

  function selectNode(node) {
    setSelectedId(node.id);
    setEvidenceEdge(null);
  }

  function toggleCanvasNode(node) {
    setSelectedId((current) => current === node.id ? null : node.id);
    setEvidenceEdge(null);
  }

  function toggleFilter(value, values, setter) {
    setter(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  }

  function clearFilters() {
    setQuery('');
    setSystemFilters([]);
    setTypeFilters([]);
  }

  function exportReport() {
    const report = {
      exportedAt: new Date().toISOString(),
      summary: model.summary,
      selectedNode: selected || null,
      lineage: lineage || null,
      relationships: relationships.map((edge) => ({
        ...edge,
        fromName: index.nodes.get(edge.source)?.name,
        toName: index.nodes.get(edge.target)?.name,
      })),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'controlgraph-report.json';
    link.click();
    URL.revokeObjectURL(url);
  }

  function runValidation() {
    const issueCount = model.nodes.filter((node) => node.kind === 'MAPPING_ISSUE').length;
    setNotice(`Validation is complete. ${issueCount} mapping ${issueCount === 1 ? 'issue' : 'issues'} found.`);
  }

  function replaceGraph(nextModel) {
    setModel(nextModel);
    setSelectedId((current) => {
      if (nextModel.nodes.some((node) => node.id === current)) return current;
      return nextModel.nodes.find((node) => node.kind === 'SOURCE_DEVICE')?.id
        || nextModel.nodes[0]?.id
        || null;
    });
    setEvidenceEdge(null);
  }

  return <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
    <AppHeader
      onImport={() => setImportOpen(true)}
      onValidate={runValidation}
      onExport={exportReport}
    />

    <Box sx={{
      display: 'grid',
      gridTemplateColumns: { xs: '1fr', lg: '300px minmax(0, 1fr) 330px' },
      gridTemplateRows: { xs: 'auto', lg: 'minmax(430px, 1fr) 285px' },
      height: { lg: 'calc(100vh - 73px)' },
      minHeight: 690,
    }}>
      <ControlBrowser
        query={query}
        setQuery={setQuery}
        systemFilters={systemFilters}
        typeFilters={typeFilters}
        toggleSystem={(value) => toggleFilter(value, systemFilters, setSystemFilters)}
        toggleType={(value) => toggleFilter(value, typeFilters, setTypeFilters)}
        clearFilters={clearFilters}
        hasActiveFilters={hasActiveFilters}
        treeItems={browserTree}
        resultCount={browserNodes.length}
        nodeIndex={index.nodes}
        selectedId={selectedId}
        selectNode={selectNode}
      />

      <LineagePanel
        model={model}
        index={index}
        selectedId={selectedId}
        graphView={graphView}
        traceMode={traceMode}
        systemFilters={systemFilters}
        typeFilters={typeFilters}
        selectNode={toggleCanvasNode}
        inspectEdge={setEvidenceEdge}
      />

      <NodeInspector
        node={selected}
        status={selectedStatus}
        upstreamCount={upstreamCount}
        downstreamCount={downstreamCount}
        traceMode={traceMode}
        showTrace={setTraceMode}
      />

      <RelationshipTable
        index={index}
        relationships={relationships}
        totalRelationships={totalRelationships}
        selectedEdge={evidenceEdge}
        inspectEdge={setEvidenceEdge}
      />
    </Box>

    <EvidenceDialog edge={evidenceEdge} index={index} close={() => setEvidenceEdge(null)} />
    <ImportProjectDialog
      open={importOpen}
      close={() => setImportOpen(false)}
      onGraphChange={replaceGraph}
      notify={setNotice}
    />
    <Snackbar open={Boolean(notice)} autoHideDuration={4000} onClose={() => setNotice('')} message={notice} />
  </Box>;
}
