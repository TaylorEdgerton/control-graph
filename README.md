# ControlGraph

ControlGraph is a local proof of concept. It maps SEL RTAC configuration data to Ignition 8.1 and 8.3 Gateway tags. It uses exact communication identity when the source data contains enough information. It shows unresolved and ambiguous mappings.

The user interface uses React and Material UI. The API uses FastAPI. OpenAPI documentation is available at `/docs`.

## Quick start

Use Python 3.10 or later and Node.js 20 or later.

```bash
make install
make run
```

Open `http://127.0.0.1:8765`. Open `http://127.0.0.1:8765/docs` for the API documentation.

`make run` uses the demonstration files. The demonstration contains one complete DNP3 lineage and one unresolved Ignition source. Use **Import Project** in the application to stage one or more `.gwbk` files, review the detected version and configuration format, select the tag providers, and add the backups to the analysis. Imported backups can be removed later.

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

The SEL parser reads XML elements with common device, point, tag, mapping, POU, variable, and Structured Text fields. 

The Ignition parser extracts a `.gwbk` archive and reads the relevant filesystem resources. It resolves UDT parameters and member overrides. The resolver supports DNP3, Modbus, and OPC item identities.

