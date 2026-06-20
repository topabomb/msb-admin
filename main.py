import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from microsandbox import Sandbox, SecurityProfile, Volume, Image, Snapshot

SDK_TIMEOUT = 60
EXPORT_DIR = "/tmp/msb-admin-exports"
templates = Jinja2Templates(directory="templates")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _extract_image(cfg: dict) -> str:
    img = cfg.get("image", {})
    if isinstance(img, dict):
        for kind, val in img.items():
            if isinstance(val, dict) and "reference" in val:
                return val["reference"]
            if isinstance(val, str):
                return val
    return str(img)


def _format_ports(ports) -> str:
    if not ports:
        return "none"
    parts = []
    for guest, host in ports.items():
        parts.append(f"{host}:{guest}")
    return ", ".join(parts)


def _sb_to_dict(h) -> dict:
    data = {"name": h.name, "status": h.status}
    try:
        cfg = json.loads(h.config_json)
        data["image"] = _extract_image(cfg)
        data["cpus"] = cfg.get("cpus", "?")
        data["memory_mib"] = cfg.get("memory_mib", "?")
        data["hostname"] = cfg.get("hostname") or "(auto)"
        data["security"] = cfg.get("security_profile", "default")
        raw_ports = cfg.get("ports") or {}
        data["ports"] = raw_ports
        data["ports_str"] = _format_ports(raw_ports)
        data["env"] = dict(cfg.get("env", []))
        data["workdir"] = cfg.get("workdir") or "/"
        data["shell"] = cfg.get("shell") or "/bin/sh"
        data["idle_timeout"] = cfg.get("policy", {}).get("idle_timeout_secs")
        data["max_duration"] = cfg.get("policy", {}).get("max_duration_secs")
        data["labels"] = cfg.get("labels") or {}
    except Exception:
        data["image"] = "?"
        data["cpus"] = "?"
        data["memory_mib"] = "?"
        data["hostname"] = ""
        data["security"] = "default"
        data["ports"] = {}
        data["ports_str"] = "?"
        data["env"] = {}
        data["workdir"] = "/"
        data["shell"] = "/bin/sh"
        data["idle_timeout"] = None
        data["max_duration"] = None
        data["labels"] = {}
    try:
        ts = h.created_at / 1000
        data["created_at"] = datetime.fromtimestamp(ts, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except Exception:
        data["created_at"] = _now()
    return data


def _render(template_name: str, **context) -> str:
    tpl = templates.get_template(template_name)
    return tpl.render(**context)


# ── Timeout & Recovery ────────────────────────────────────────────────────

async def _with_timeout(coro, timeout, *, name=None, recovery=None):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        if recovery:
            try:
                await recovery(name)
            except Exception:
                pass
        raise


async def _recovery_remove(name: str) -> None:
    if not name:
        return
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=5)
        try:
            await asyncio.wait_for(h.kill(), timeout=10)
        except Exception:
            pass
        await asyncio.wait_for(h.remove(), timeout=10)
    except Exception:
        pass


async def _safe_connect(name: str) -> tuple:
    h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
    if h.status != "running":
        sb = await asyncio.wait_for(Sandbox.start(name), timeout=SDK_TIMEOUT)
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        sb = await asyncio.wait_for(h.connect(), timeout=10)
    else:
        sb = await asyncio.wait_for(h.connect(), timeout=10)
    return h, sb


async def _table_refresh():
    handles = await Sandbox.list()
    sandboxes = [_sb_to_dict(h) for h in handles]
    return HTMLResponse(
        _render("sandbox_table.html", sandboxes=sandboxes),
        headers={"HX-Trigger": "toast"},
    )


async def _try_stop_and_remove(name: str) -> None:
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        try:
            await asyncio.wait_for(h.stop(), timeout=15)
        except Exception:
            pass
        await asyncio.wait_for(h.remove(), timeout=10)
    except Exception:
        pass


async def _clean_export_tempfiles():
    if os.path.isdir(EXPORT_DIR):
        for f in os.listdir(EXPORT_DIR):
            try:
                os.remove(os.path.join(EXPORT_DIR, f))
            except Exception:
                pass


async def _clean_orphan_pulls():
    try:
        handles = await Sandbox.list()
        for h in handles:
            if h.name and h.name.startswith("_pull_"):
                try:
                    await _try_stop_and_remove(h.name)
                except Exception:
                    pass
    except Exception:
        pass


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    await _clean_orphan_pulls()
    await _clean_export_tempfiles()
    yield


app = FastAPI(title="msb-admin", lifespan=lifespan)


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, search: str = ""):
    handles = await Sandbox.list()
    sandboxes = [_sb_to_dict(h) for h in handles]
    if search:
        search_lower = search.lower()
        sandboxes = [s for s in sandboxes if search_lower in s["name"].lower() or search_lower in s["image"].lower()]
    running = sum(1 for s in sandboxes if s["status"] == "running")
    stopped = sum(1 for s in sandboxes if s["status"] == "stopped")
    html = _render(
        "index.html",
        request=request,
        sandboxes=sandboxes,
        running=running,
        stopped=stopped,
        total=len(sandboxes),
        search=search,
    )
    return HTMLResponse(html)


@app.get("/create-form", response_class=HTMLResponse)
async def create_form(request: Request):
    vols = await Volume.list()
    return HTMLResponse(_render("create_form.html", request=request, volumes=vols))


@app.get("/sandboxes/table", response_class=HTMLResponse)
async def sandbox_table(request: Request, search: str = ""):
    handles = await Sandbox.list()
    sandboxes = [_sb_to_dict(h) for h in handles]
    if search:
        search_lower = search.lower()
        sandboxes = [s for s in sandboxes if search_lower in s["name"].lower() or search_lower in s["image"].lower()]
    return HTMLResponse(_render("sandbox_table.html", sandboxes=sandboxes))


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    try:
        await asyncio.wait_for(Sandbox.list(), timeout=5)
        sdk_ok = True
    except Exception:
        sdk_ok = False
    return {
        "status": "ok" if sdk_ok else "degraded",
        "sdk": sdk_ok,
        "timestamp": _now(),
    }


# ── Sandbox API ──────────────────────────────────────────────────────────────

@app.get("/api/sandboxes", response_class=JSONResponse)
async def list_sandboxes(offset: int = 0, limit: int = 100, search: str = ""):
    handles = await Sandbox.list()
    sandboxes = [_sb_to_dict(h) for h in handles]
    if search:
        search_lower = search.lower()
        sandboxes = [s for s in sandboxes if search_lower in s["name"].lower() or search_lower in s["image"].lower()]
    total = len(sandboxes)
    sandboxes = sandboxes[offset:offset + limit]
    return {"sandboxes": sandboxes, "total": total, "offset": offset, "limit": limit}


@app.get("/api/sandboxes/{name}", response_class=JSONResponse)
async def get_sandbox(name: str):
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        return _sb_to_dict(h)
    except Exception as e:
        return JSONResponse({"error": f"sandbox not found: {e}"}, status_code=404)


# ── Bulk Operations ─────────────────────────────────────────────────────────

@app.post("/api/sandboxes/batch/{action}")
async def batch_sandboxes(action: str, names: str = Form(...)):
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    results = []
    for name in name_list:
        try:
            h = await asyncio.wait_for(Sandbox.get(name), timeout=5)
            if action == "stop":
                await asyncio.wait_for(h.stop(), timeout=SDK_TIMEOUT)
            elif action == "kill":
                await asyncio.wait_for(h.kill(), timeout=SDK_TIMEOUT)
            elif action == "delete":
                await _try_stop_and_remove(name)
            elif action == "start":
                await asyncio.wait_for(Sandbox.start(name), timeout=SDK_TIMEOUT)
            else:
                return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
            results.append({"name": name, "status": "ok"})
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)})
    return {"results": results, "action": action}


# ── Sandbox Detail ───────────────────────────────────────────────────────────

@app.get("/sandboxes/{name}", response_class=HTMLResponse)
async def sandbox_detail(request: Request, name: str):
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        data = _sb_to_dict(h)
    except Exception:
        data = {"name": name, "status": "unknown", "image": "?", "cpus": "?", "memory_mib": "?", "hostname": "", "security": "default", "ports": {}, "ports_str": "?", "env": {}, "workdir": "/", "shell": "/bin/sh", "idle_timeout": None, "max_duration": None, "created_at": "", "labels": {}}
    return HTMLResponse(
        _render("detail.html", request=request, sb=data, name=name)
    )


# ── Create Sandbox ───────────────────────────────────────────────────────────

@app.post("/sandboxes/create")
async def create_sandbox(
    name: str = Form(...),
    image: str = Form(...),
    cpus: int = Form(1),
    memory: int = Form(512),
    ports: str = Form(""),
    hostname: str = Form(""),
    security: str = Form("default"),
    env_json: str = Form(""),
    workdir: str = Form(""),
    shell: str = Form(""),
    user: str = Form(""),
    idle_timeout: int = Form(0),
    max_duration: int = Form(0),
    volumes: str = Form(""),
):
    kwargs = dict(
        name=name,
        image=image,
        cpus=cpus,
        memory=memory,
        detached=True,
        idle_timeout=idle_timeout if idle_timeout > 0 else 300,
    )
    if max_duration > 0:
        kwargs["max_duration"] = max_duration
    if hostname:
        kwargs["hostname"] = hostname
    if security == "restricted":
        kwargs["security"] = SecurityProfile.RESTRICTED
    if workdir:
        kwargs["workdir"] = workdir
    if shell:
        kwargs["shell"] = shell
    if user:
        kwargs["user"] = user
    if env_json:
        try:
            kwargs["env"] = json.loads(env_json)
        except json.JSONDecodeError:
            pass
    if ports:
        port_map = {}
        for part in ports.split(","):
            part = part.strip()
            if ":" in part:
                h_port, g_port = part.split(":", 1)
                port_map[int(h_port.strip())] = int(g_port.strip())
            elif part:
                port_map[int(part)] = int(part)
        if port_map:
            kwargs["ports"] = port_map
    if volumes:
        mounts = {}
        for vol_ref in volumes.split(","):
            vol_ref = vol_ref.strip()
            if vol_ref:
                mounts[vol_ref] = Volume.named(vol_ref, mode="ensure-exists")
        if mounts:
            kwargs["volumes"] = mounts

    try:
        sb = await _with_timeout(
            Sandbox.create(**kwargs), timeout=SDK_TIMEOUT,
            name=name, recovery=_recovery_remove,
        )
        await sb.detach()
    except asyncio.TimeoutError:
        return HTMLResponse(
            _render("exec_result.html", output="Error: sandbox creation timed out (sandbox may still exist)", exit_code=-1, command="create"),
            status_code=504,
        )
    except Exception as e:
        return HTMLResponse(
            _render("exec_result.html", output=f"Error creating sandbox: {e}", exit_code=-1, command="create"),
            status_code=400,
        )
    return await _table_refresh()


# ── Sandbox Lifecycle ────────────────────────────────────────────────────────

@app.post("/sandboxes/{name}/stop")
async def stop_sandbox(name: str):
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        await asyncio.wait_for(h.stop(), timeout=SDK_TIMEOUT)
    except Exception:
        pass
    return await _table_refresh()


@app.post("/sandboxes/{name}/start")
async def start_sandbox(name: str):
    try:
        await asyncio.wait_for(Sandbox.start(name), timeout=SDK_TIMEOUT)
    except Exception:
        pass
    return await _table_refresh()


@app.post("/sandboxes/{name}/kill")
async def kill_sandbox(name: str):
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        await asyncio.wait_for(h.kill(), timeout=SDK_TIMEOUT)
    except Exception:
        pass
    return await _table_refresh()


@app.post("/sandboxes/{name}/delete")
async def delete_sandbox(name: str):
    await _try_stop_and_remove(name)
    return await _table_refresh()


# ── Exec ─────────────────────────────────────────────────────────────────────

@app.post("/sandboxes/{name}/exec")
async def exec_in_sandbox(name: str, command: str = Form(...)):
    try:
        h, sb = await _safe_connect(name)
        result = await asyncio.wait_for(sb.exec("sh", ["-c", command], timeout=30), timeout=35)
        output = result.stdout_text + result.stderr_text
        exit_code = result.exit_code
        return HTMLResponse(
            _render("exec_result.html", output=output or "(no output)", exit_code=exit_code, command=command)
        )
    except asyncio.TimeoutError:
        return HTMLResponse(
            _render("exec_result.html", output="Error: command timed out", exit_code=-1, command=command)
        )
    except Exception as e:
        return HTMLResponse(
            _render("exec_result.html", output=f"Error: {e}", exit_code=-1, command=command)
        )


@app.get("/sandboxes/{name}/exec-panel")
async def exec_panel(request: Request, name: str):
    return HTMLResponse(_render("exec_panel.html", name=name))


@app.get("/sandboxes/{name}/terminal")
async def terminal_page(request: Request, name: str):
    return HTMLResponse(_render("terminal.html", name=name))


# ── Panels ───────────────────────────────────────────────────────────────────

@app.get("/sandboxes/{name}/logs-panel")
async def logs_panel(request: Request, name: str):
    return HTMLResponse(_render("logs_panel.html", name=name))


@app.get("/sandboxes/{name}/metrics-panel")
async def metrics_panel(request: Request, name: str):
    return HTMLResponse(_render("metrics_panel.html", name=name))


@app.get("/sandboxes/{name}/fs-panel")
async def fs_panel(request: Request, name: str):
    return HTMLResponse(_render("fs_panel.html", name=name))


# ── Filesystem API ───────────────────────────────────────────────────────────

@app.get("/api/sandboxes/{name}/fs/list")
async def fs_list(name: str, path: str = "/"):
    try:
        h, sb = await _safe_connect(name)
        entries = await asyncio.wait_for(sb.fs.list(path), timeout=10)
        items = []
        for e in entries:
            items.append({
                "name": Path(e.path).name or e.path,
                "path": e.path,
                "kind": e.kind,
                "size": e.size,
                "modified": e.modified,
            })
        return items
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/sandboxes/{name}/fs/read")
async def fs_read(name: str, path: str):
    try:
        h, sb = await _safe_connect(name)
        content = await asyncio.wait_for(sb.fs.read_text(path), timeout=10)
        return {"content": content, "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/sandboxes/{name}/fs/write")
async def fs_write(name: str, path: str = Form(...), content: str = Form(...)):
    try:
        h, sb = await _safe_connect(name)
        await asyncio.wait_for(sb.fs.write(path, content.encode()), timeout=10)
        return {"status": "written", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/sandboxes/{name}/fs/mkdir")
async def fs_mkdir(name: str, path: str = Form(...)):
    try:
        h, sb = await _safe_connect(name)
        await asyncio.wait_for(sb.fs.mkdir(path), timeout=10)
        return {"status": "created", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/sandboxes/{name}/fs/remove")
async def fs_remove(name: str, path: str = Form(...)):
    try:
        h, sb = await _safe_connect(name)
        try:
            await asyncio.wait_for(sb.fs.remove(path), timeout=10)
        except Exception:
            await asyncio.wait_for(sb.fs.remove_dir(path), timeout=10)
        return {"status": "removed", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/sandboxes/{name}/fs/upload")
async def fs_upload(name: str, path: str = Form(...), file: UploadFile = File(...)):
    try:
        data = await file.read()
        h, sb = await _safe_connect(name)
        await asyncio.wait_for(sb.fs.write(path, data), timeout=30)
        return {"status": "uploaded", "path": path, "bytes": len(data)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/sandboxes/{name}/fs/stat")
async def fs_stat(name: str, path: str = "/"):
    try:
        h, sb = await _safe_connect(name)
        s = await asyncio.wait_for(sb.fs.stat(path), timeout=10)
        return {"path": path, "kind": s.kind, "size": s.size, "modified": s.modified, "readonly": s.readonly}
    except Exception as e:
        return {"error": str(e)}


# ── Snapshots ────────────────────────────────────────────────────────────────

@app.get("/api/snapshots", response_class=JSONResponse)
async def list_snapshots():
    handles = await Snapshot.list()
    return [
        {
            "digest": h.digest,
            "name": h.name,
            "size_bytes": h.size_bytes,
            "created_at": h.created_at,
            "image_ref": h.image_ref,
        }
        for h in handles
    ]


@app.post("/api/snapshots")
async def create_snapshot(source: str = Form(...), name: str = Form("")):
    try:
        sb = await asyncio.wait_for(Sandbox.get(source), timeout=10)
        snap_name = name or f"{sb.name}-{int(datetime.now().timestamp())}"
        snap = await asyncio.wait_for(Snapshot.create(sb.name, name=snap_name), timeout=SDK_TIMEOUT)
        return {"digest": snap.digest, "name": snap_name, "path": snap.path, "size_bytes": snap.size_bytes}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/snapshots/{name_or_digest}/export")
async def export_snapshot(name_or_digest: str):
    try:
        handle = await asyncio.wait_for(Snapshot.get(name_or_digest), timeout=10)
        os.makedirs(EXPORT_DIR, exist_ok=True)
        out_path = os.path.join(EXPORT_DIR, f"snap-{name_or_digest[:16]}.tar")
        await asyncio.wait_for(Snapshot.export(name_or_digest, out_path), timeout=SDK_TIMEOUT)
        if not os.path.exists(out_path):
            return JSONResponse({"error": "export failed: file not created"}, status_code=500)

        async def file_stream():
            with open(out_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
            try:
                os.remove(out_path)
            except Exception:
                pass

        return StreamingResponse(file_stream(), media_type="application/octet-stream",
                                 headers={"Content-Disposition": f"attachment; filename=snapshot-{name_or_digest[:16]}.tar"})
    except asyncio.TimeoutError:
        return JSONResponse({"error": "export timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/snapshots/restore")
async def restore_snapshot(digest: str = Form(...), name: str = Form("")):
    try:
        handle = await asyncio.wait_for(Snapshot.get(digest), timeout=10)
        snap = await asyncio.wait_for(handle.open(), timeout=10)
        image_ref = snap.image_ref or "alpine"
        sb_name = name or f"restore-{digest[:12]}-{int(datetime.now().timestamp())}"
        sb = await _with_timeout(
            Sandbox.create(name=sb_name, image=image_ref, detached=True, idle_timeout=300),
            timeout=SDK_TIMEOUT,
            name=sb_name, recovery=_recovery_remove,
        )
        await sb.detach()
        return {"name": sb_name, "image": image_ref, "status": "created"}
    except asyncio.TimeoutError:
        return JSONResponse({"error": "restore timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/snapshots/{name_or_digest}/verify")
async def verify_snapshot(name_or_digest: str):
    try:
        handle = await asyncio.wait_for(Snapshot.get(name_or_digest), timeout=10)
        snap = await asyncio.wait_for(handle.open(), timeout=10)
        report = await asyncio.wait_for(snap.verify(), timeout=SDK_TIMEOUT)
        return report
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/snapshots/{digest_or_name}")
async def delete_snapshot(digest_or_name: str):
    try:
        await asyncio.wait_for(Snapshot.remove(digest_or_name), timeout=10)
        return {"status": "removed"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/snapshots")
async def snapshots_page(request: Request):
    handles = await Snapshot.list()
    snaps = [
        {
            "digest": h.digest,
            "name": h.name,
            "size_bytes": h.size_bytes,
            "created_at": h.created_at,
            "image_ref": h.image_ref,
        }
        for h in handles
    ]
    return HTMLResponse(_render("snapshots.html", request=request, snapshots=snaps))


@app.post("/sandboxes/{name}/snapshot")
async def snapshot_sandbox(name: str, snapshot_name: str = Form("")):
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        snap_name = snapshot_name or f"{h.name}-{int(datetime.now().timestamp())}"
        snap = await asyncio.wait_for(Snapshot.create(h.name, name=snap_name), timeout=SDK_TIMEOUT)
        return HTMLResponse(
            _render("exec_result.html", output=f"Snapshot created: {snap.digest[:16]}...", exit_code=0, command=f"snapshot {name}")
        )
    except Exception as e:
        return HTMLResponse(
            _render("exec_result.html", output=f"Error: {e}", exit_code=-1, command=f"snapshot {name}")
        )


# ── Volumes ──────────────────────────────────────────────────────────────────

@app.get("/api/volumes", response_class=JSONResponse)
async def list_volumes():
    vols = await Volume.list()
    return [
        {
            "name": v.name,
            "kind": v.kind,
            "used_bytes": v.used_bytes,
            "capacity_bytes": v.capacity_bytes,
            "created_at": v.created_at,
        }
        for v in vols
    ]


@app.get("/api/volumes/{name}", response_class=JSONResponse)
async def get_volume(name: str):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        return {
            "name": v.name,
            "kind": v.kind,
            "used_bytes": v.used_bytes,
            "capacity_bytes": v.capacity_bytes,
            "created_at": v.created_at,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/volumes")
async def create_volume(name: str = Form(...), kind: str = Form("dir")):
    try:
        vol = await asyncio.wait_for(Volume.create(name=name, kind=kind), timeout=30)
        return {"name": vol.name, "path": vol.path, "kind": kind}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/volumes/{name}")
async def delete_volume(name: str):
    try:
        await asyncio.wait_for(Volume.remove(name), timeout=10)
        return {"status": "removed", "name": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/volumes/{name}/fs/list")
async def volume_fs_list(name: str, path: str = "/"):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        entries = await asyncio.wait_for(v.fs.list(path), timeout=10)
        items = []
        for e in entries:
            items.append({
                "name": Path(e.path).name or e.path,
                "path": e.path,
                "kind": e.kind,
                "size": e.size,
                "modified": e.modified,
            })
        return items
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/volumes/{name}/fs/read")
async def volume_fs_read(name: str, path: str):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        content = await asyncio.wait_for(v.fs.read_text(path), timeout=10)
        return {"content": content, "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/volumes/{name}/fs/write")
async def volume_fs_write(name: str, path: str = Form(...), content: str = Form(...)):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        await asyncio.wait_for(v.fs.write(path, content.encode()), timeout=10)
        return {"status": "written", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/volumes/{name}/fs/mkdir")
async def volume_fs_mkdir(name: str, path: str = Form(...)):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        await asyncio.wait_for(v.fs.mkdir(path), timeout=10)
        return {"status": "created", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/volumes/{name}/fs/remove")
async def volume_fs_remove(name: str, path: str = Form(...)):
    try:
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        await asyncio.wait_for(v.fs.remove_file(path), timeout=10)
        return {"status": "removed", "path": path}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/volumes/{name}/fs/upload")
async def volume_fs_upload(name: str, path: str = Form(...), file: UploadFile = File(...)):
    try:
        data = await file.read()
        v = await asyncio.wait_for(Volume.get(name), timeout=10)
        await asyncio.wait_for(v.fs.write(path, data), timeout=30)
        return {"status": "uploaded", "path": path, "bytes": len(data)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/volumes")
async def volumes_page(request: Request):
    vols = await Volume.list()
    return HTMLResponse(_render("volumes.html", request=request, volumes=vols))


# ── Images ───────────────────────────────────────────────────────────────────

@app.get("/api/images", response_class=JSONResponse)
async def list_images():
    images = await Image.list()
    return [
        {
            "reference": img.reference,
            "size_bytes": img.size_bytes,
            "layer_count": img.layer_count,
            "architecture": img.architecture,
            "os": img.os,
            "created_at": img.created_at,
        }
        for img in images
    ]


@app.get("/api/images/{reference:path}", response_class=JSONResponse)
async def get_image(reference: str):
    try:
        img = await asyncio.wait_for(Image.get(reference), timeout=10)
        detail = await asyncio.wait_for(img.inspect(), timeout=10)
        return {
            "reference": img.reference,
            "size_bytes": img.size_bytes,
            "layer_count": img.layer_count,
            "architecture": img.architecture,
            "os": img.os,
            "created_at": img.created_at,
            "config": {
                "env": detail.config.env if detail.config else [],
                "cmd": detail.config.cmd if detail.config else [],
                "entrypoint": detail.config.entrypoint if detail.config else [],
                "working_dir": detail.config.working_dir if detail.config else None,
                "user": detail.config.user if detail.config else None,
            },
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/images/pull")
async def pull_image(reference: str = Form(...)):
    try:
        temp_name = f"_pull_{int(datetime.now().timestamp())}"
        sb = await _with_timeout(
            Sandbox.create(name=temp_name, image=reference, detached=True, idle_timeout=60, max_duration=120),
            timeout=SDK_TIMEOUT + 120,
            name=temp_name, recovery=_recovery_remove,
        )
        await sb.detach()
        await _try_stop_and_remove(temp_name)
        return {"reference": reference, "status": "pulled"}
    except asyncio.TimeoutError:
        return JSONResponse({"error": "image pull timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/images/prune")
async def prune_images():
    try:
        report = await asyncio.wait_for(Image.prune(), timeout=SDK_TIMEOUT)
        return {
            "images_removed": report.image_refs_removed,
            "manifests_removed": report.manifests_removed,
            "layers_removed": report.layers_removed,
            "bytes_reclaimed": report.bytes_reclaimed,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/images/{reference:path}")
async def delete_image(reference: str):
    try:
        await asyncio.wait_for(Image.remove(reference), timeout=30)
        return {"status": "removed", "reference": reference}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/images")
async def images_page(request: Request):
    images = await Image.list()
    return HTMLResponse(_render("images.html", request=request, images=images))


# ── WebSocket: Logs ─────────────────────────────────────────────────────────

@app.websocket("/ws/logs/{name}")
async def ws_logs(websocket: WebSocket, name: str):
    await websocket.accept()
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        last_send = 0
        while True:
            try:
                entries = await asyncio.wait_for(h.logs(tail=50), timeout=10)
                lines = []
                for e in entries:
                    ts = e.timestamp_ms / 1000
                    if ts > last_send:
                        lines.append(
                            f"[{datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M:%S')}] {e.text().rstrip()}"
                        )
                        last_send = ts
                if lines:
                    await websocket.send_text("\n".join(lines[-20:]))
                await asyncio.sleep(1)
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"[system] Error: {e}")
        except Exception:
            pass


# ── WebSocket: Metrics ───────────────────────────────────────────────────────

@app.websocket("/ws/metrics/{name}")
async def ws_metrics(websocket: WebSocket, name: str):
    await websocket.accept()
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        sb = await asyncio.wait_for(h.connect(), timeout=10)
        metrics = await sb.metrics_stream(interval=2.0)
        async for m in metrics:
            data = {
                "cpu": m.cpu_percent,
                "memory_mb": round(m.memory_bytes / 1024 / 1024, 1) if m.memory_bytes else 0,
                "memory_limit_mb": round(m.memory_limit_bytes / 1024 / 1024, 1) if m.memory_limit_bytes else 0,
                "rx_bytes": m.net_rx_bytes,
                "tx_bytes": m.net_tx_bytes,
                "disk_read_mb": round(m.disk_read_bytes / 1024 / 1024, 2) if m.disk_read_bytes else 0,
                "disk_write_mb": round(m.disk_write_bytes / 1024 / 1024, 2) if m.disk_write_bytes else 0,
                "timestamp_ms": m.timestamp_ms,
            }
            await websocket.send_text(json.dumps(data))
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"error": str(e)}))
        except Exception:
            pass


# ── WebSocket: Terminal ─────────────────────────────────────────────────────

@app.websocket("/ws/terminal/{name}")
async def ws_terminal(websocket: WebSocket, name: str):
    await websocket.accept()
    read_task = None
    try:
        h = await asyncio.wait_for(Sandbox.get(name), timeout=10)
        sb = await asyncio.wait_for(h.connect(), timeout=10)
        handle = await asyncio.wait_for(sb.exec_stream("sh", tty=True), timeout=30)
        read_task = asyncio.create_task(_terminal_reader(handle, websocket))

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=3600)
                data = json.loads(msg)
                if data.get("type") == "input":
                    await handle.stdin.write(data["data"].encode())
                    await handle.stdin.drain()
                elif data.get("type") == "resize":
                    if hasattr(handle, "resize"):
                        await handle.resize(data.get("cols", 80), data.get("rows", 24))
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        if read_task:
            read_task.cancel()
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "data": str(e)}))
        except Exception:
            pass
    finally:
        if read_task:
            read_task.cancel()


async def _terminal_reader(handle, ws: WebSocket):
    try:
        async for chunk in handle.stdout:
            await ws.send_text(
                json.dumps(
                    {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                )
            )
        for chunk in handle.stderr:
            await ws.send_text(
                json.dumps(
                    {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                )
            )
        exit_code = await handle.wait()
        await ws.send_text(json.dumps({"type": "exit", "code": exit_code}))
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
