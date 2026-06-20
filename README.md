# MicroSandbox Admin UI

Web-based administration interface for **MicroSandbox** — a lightweight sandboxed container runtime. Built with **FastAPI + Jinja2 + HTMX + xterm.js**, this dashboard provides real-time management of sandboxes, images, snapshots, volumes, and more via the official `microsandbox` Python SDK.

## Features

### Sandbox Lifecycle Management
- Create, start, stop, kill, delete sandboxes
- Bulk batch operations: start/stop/kill/delete by comma-separated names
- Paginated sandbox list with search/filter (`?offset=&limit=&search=`)
- Real-time logs via WebSocket
- Real-time metrics (CPU, memory) via WebSocket
- Interactive web terminal via xterm.js + WebSocket
- Execute arbitrary commands and view results

### Image Management
- List all available images with metadata
- Pull images from registries (via temporary sandbox + auto-cleanup)
- Prune unused image layers to reclaim disk space

### Snapshot Management
- List, create, delete snapshots
- Export snapshots as downloadable files with temp-file cleanup
- Restore sandboxes from snapshots
- Verify snapshot integrity

### Volume Management
- List volumes with metadata
- Browse volume filesystem (tree view + content display)
- File operations: read, write, create, rename, delete, upload
- Directory operations: create, rename

### Health & Diagnostics
- SDK connectivity health check endpoint
- Detailed error reporting with HTTP status codes (400/404/409/504)

## Quick Start

### Prerequisites

- Python 3.11+
- MicroSandbox SDK installed and configured
- Access to a MicroSandbox runtime

### Installation

```bash
# Clone the repository
git clone https://github.com/topabomb/msb-admin.git
cd msb-admin

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Set the MicroSandbox SDK endpoint via environment variable:

```bash
export MSB_ENDPOINT=http://localhost:16379
```

Or edit the default in `main.py` (line: `MSB_ENDPOINT = "http://localhost:16379"`).

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Open http://localhost:8080 in your browser.

### Run Tests

```bash
pytest -v
```

All 38 test cases cover every feature endpoint and edge case.

## Docker

### Build

```bash
docker build -t msb-admin .
```

### Run

```bash
docker run -d \
  --name msb-admin \
  -p 8080:8080 \
  -e MSB_ENDPOINT=http://host.docker.internal:16379 \
  msb-admin
```

### Persist Data

Mount a volume to persist the project (optional, for development):

```bash
docker run -d \
  --name msb-admin \
  -p 8080:8080 \
  -v $(pwd):/msb-admin \
  -e MSB_ENDPOINT=http://host.docker.internal:16379 \
  msb-admin
```

## API Endpoints

### Sandboxes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sandboxes` | List sandboxes (supports `?offset=&limit=&search=`) |
| GET | `/api/sandboxes/{name}` | Get sandbox details |
| POST | `/api/sandboxes/create` | Create a new sandbox |
| POST | `/api/sandboxes/{name}/start` | Start a sandbox |
| POST | `/api/sandboxes/{name}/stop` | Stop a sandbox |
| POST | `/api/sandboxes/{name}/kill` | Kill a sandbox |
| DELETE | `/api/sandboxes/{name}` | Delete a sandbox |
| POST | `/api/sandboxes/batch/{action}` | Bulk action (start/stop/kill/delete) |
| GET | `/api/sandboxes/{name}/exec` | Execute command in sandbox |
| POST | `/api/sandboxes/{name}/exec` | Execute command with custom input |

### Images

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/images` | List all images |
| POST | `/api/images/pull` | Pull an image |
| POST | `/api/images/prune` | Prune unused images |

### Snapshots

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/snapshots` | List snapshots |
| POST | `/api/snapshots/create` | Create snapshot from sandbox |
| GET | `/api/snapshots/{name}/export` | Download snapshot file |
| POST | `/api/snapshots/restore` | Restore sandbox from snapshot |
| GET | `/api/snapshots/{name}/verify` | Verify snapshot integrity |
| DELETE | `/api/snapshots/{name}` | Delete snapshot |

### Volumes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/volumes` | List volumes |
| POST | `/api/volumes/{volume_id}/fs` | Browse filesystem |
| POST | `/api/volumes/{volume_id}/fs/write` | Write file content |
| POST | `/api/volumes/{volume_id}/fs/mkdir` | Create directory |
| POST | `/api/volumes/{volume_id}/fs/remove_file` | Remove file |
| POST | `/api/volumes/{volume_id}/fs/upload` | Upload file |

### WebSocket

| Path | Description |
|------|-------------|
| `/ws/logs/{name}` | Real-time sandbox logs |
| `/ws/metrics/{name}` | Real-time sandbox metrics (CPU, memory) |
| `/ws/terminal/{name}` | Interactive terminal session |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | SDK connectivity health check |

## Architecture

```
msb-admin/
├── main.py              # FastAPI application (all routes, handlers, WS)
├── test_app.py          # 38 pytest test cases
├── requirements.txt     # Python dependencies
├── pytest.ini           # Test configuration
├── templates/           # Jinja2 HTML templates
│   ├── index.html       # Dashboard home with search
│   ├── sandbox_table.html
│   ├── detail.html      # Sandbox detail with ports, logs, metrics, terminal
│   ├── create_form.html
│   ├── logs_panel.html
│   ├── metrics_panel.html
│   ├── terminal.html
│   ├── exec_panel.html
│   ├── exec_result.html
│   ├── fs_panel.html
│   ├── images.html
│   ├── snapshots.html
│   └── volumes.html
├── static/              # Static assets
└── .gitignore
```

### Key Design Decisions

- **Pure server-side rendering** — HTMX for dynamic updates, no JavaScript framework
- **SDK timeout recovery** — `_with_timeout(coro, timeout, name=, recovery=)` wrapper with per-call cleanup on timeout
- **Stale handle prevention** — `_safe_connect()` re-fetches sandbox handle after `start()` to avoid race conditions
- **Bulk operations** — Single endpoint `/api/sandboxes/batch/{action}` accepts comma-separated names
- **Export temp files** — Isolated to `/tmp/msb-admin-exports/`, cleaned on stream completion and server startup
- **Port formatting** — Internal `{guest: host}` dict converted to `"host:guest"` string for display

## License

MIT
