# ControlGraph

ControlGraph is a local proof of concept. It maps SEL RTAC configuration data to Ignition 8.3 tags. It uses exact communication identity when the source data contains enough information. It shows unresolved and ambiguous mappings. It does not use a graph database.

The user interface uses React and Material UI. The API uses FastAPI. OpenAPI documentation is available at `/docs`.

## Quick start

Use Python 3.10 or later and Node.js 20 or later.

```bash
make install
make run
```

Open `http://127.0.0.1:8765`. Open `http://127.0.0.1:8765/docs` for the API documentation.

`make run` uses the demonstration files. The demonstration contains one complete DNP3 lineage and one unresolved Ignition source.

## Use project files

Build the user interface. Then give the SEL XML file and the Ignition backup to the command.

```bash
make build
python -m controlgraph --sel plant.xml --ignition gateway.gwbk
```

The Ignition input can also be an extracted backup directory.

Export the in-memory model to JSON without the web server:

```bash
python -m controlgraph --sel plant.xml --ignition gateway.gwbk --export model.json
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

The SEL parser reads XML elements with common device, point, tag, mapping, POU, variable, and Structured Text fields. The Ignition parser safely extracts a `.gwbk` archive and reads filesystem JSON resources. It resolves UDT parameters and member overrides. The resolver supports exact DNP3, Modbus, and OPC item identities.

Vendor exports can use different field names. Add a small parser adapter when a real export uses a field that the current heuristics do not identify. Each node and relationship keeps its source file, source location, and configuration evidence.
