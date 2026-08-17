# ControlGraph

ControlGraph is a local proof of concept. It maps source-device configuration data to Ignition 8.1 and 8.3 Gateway tags. It uses exact communication identity when the source data contains enough information. It shows unresolved and ambiguous mappings.

The user interface uses React and Material UI. The API uses FastAPI. OpenAPI documentation is available at `/docs`.

## Quick start

Use Python 3.10 or later and Node.js 20 or later.

```bash
make install
make run
```

Open `http://127.0.0.1:8765`. Open `http://127.0.0.1:8765/docs` for the API documentation.

After `make run`, use **Import Project** in the application to stage one or more Ignition `.gwbk` backups or control-device `.xml` project exports. Confirming or removing a file rebuilds and validates the combined model automatically. **Run Validation** is also available to force a fresh backend relink and validation pass.

## Use project files

Build the user interface. You can optionally preload a source-device XML file, an Ignition backup, or both from the command line.

```bash
make build
python -m controlgraph --source plant.xml --ignition gateway.gwbk
```

The Ignition input can also be an extracted backup directory.

Export the in-memory model to JSON without the web server:

```bash
python -m controlgraph --source plant.xml --ignition gateway.gwbk --export model.json
```

## Development

```bash
make dev
```

Vite serves the user interface at `http://127.0.0.1:5173`. FastAPI serves the API and its documentation at port `8765`.

Use these commands for validation:

```bash
make test
make check
```

## Parser scope

The source parser reads XML elements with common device, point, tag, mapping, POU, variable, and Structured Text fields.
The Ignition parser extracts a `.gwbk` archive and reads the relevant filesystem resources. It resolves UDT parameters and member overrides. The resolver supports DNP3, Modbus, and OPC item identities.

Native Ignition driver devices are modeled as device connections. Third-party OPC UA clients are modeled separately as OPC UA server connections.
