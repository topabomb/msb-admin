import asyncio
import json
import time
from datetime import datetime

import httpx
import pytest
from microsandbox import Sandbox, Volume as MsbVolume, Snapshot

BASE_URL = "http://127.0.0.1:8080"


async def client():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        yield c


async def cleanup(name: str):
    try:
        h = await Sandbox.get(name)
        await h.kill()
    except Exception:
        pass
    try:
        await Sandbox.remove(name)
    except Exception:
        pass


async def wait_for_status(name: str, target: str, deadline_s: int = 25):
    deadline = asyncio.get_event_loop().time() + deadline_s
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        while asyncio.get_event_loop().time() < deadline:
            r = await c.get(f"/api/sandboxes/{name}")
            if r.status_code == 200 and r.json().get("status") == target:
                return
            await asyncio.sleep(0.5)
    raise AssertionError(f"sandbox {name} did not become {target} in {deadline_s}s")


@pytest.mark.asyncio
async def test_index():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "msb" in r.text.lower()


@pytest.mark.asyncio
async def test_create_form():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        r = await c.get("/create-form")
    assert r.status_code == 200
    assert "Create" in r.text


@pytest.mark.asyncio
async def test_table():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        r = await c.get("/sandboxes/table")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_sandboxes():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        r = await c.get("/api/sandboxes")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, dict)
    assert "sandboxes" in d
    assert isinstance(d["sandboxes"], list)


@pytest.mark.asyncio
async def test_create_list_detail():
    name = "pytest-create-sb"
    await cleanup(name)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine",
            "cpus": 1, "memory": 256,
        })
        assert r.status_code == 200

        await wait_for_status(name, "running")

        r = await c.get("/sandboxes/" + name)
        assert r.status_code == 200

    await cleanup(name)


@pytest.mark.asyncio
async def test_sandbox_config():
    name = "pytest-cfg-sb"
    await cleanup(name)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine",
            "cpus": 1, "memory": 256,
        })
        await wait_for_status(name, "running")

        r = await c.get(f"/api/sandboxes/{name}")
        d = r.json()
        assert d["cpus"] == 1
        assert d["memory_mib"] == 256
        assert d["image"] not in (None, "?")

    await cleanup(name)


@pytest.mark.asyncio
async def test_exec():
    name = "pytest-exec-sb"
    await cleanup(name)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine",
            "cpus": 1, "memory": 256,
        })
        await wait_for_status(name, "running")

        r = await c.post(f"/sandboxes/{name}/exec", data={"command": "echo hello_world && uname -m"})
        assert r.status_code == 200
        assert "hello_world" in r.text
        assert "Exit:" in r.text

    await cleanup(name)


@pytest.mark.asyncio
async def test_stop_start():
    name = "pytest-ss-sb"
    await cleanup(name)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine",
            "cpus": 1, "memory": 256,
        })
        await wait_for_status(name, "running")

        r = await c.post(f"/sandboxes/{name}/stop")
        assert r.status_code == 200
        await wait_for_status(name, "stopped")

        r = await c.post(f"/sandboxes/{name}/start")
        assert r.status_code == 200
        await wait_for_status(name, "running")

    await cleanup(name)


@pytest.mark.asyncio
async def test_kill_delete():
    name = "pytest-kd-sb"
    await cleanup(name)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine",
            "cpus": 1, "memory": 256,
        })
        await wait_for_status(name, "running")

        r = await c.post(f"/sandboxes/{name}/kill")
        assert r.status_code == 200

        r = await c.post(f"/sandboxes/{name}/delete")
        assert r.status_code == 200

        r = await c.get("/api/sandboxes")
        names = [s["name"] for s in r.json()["sandboxes"]]
        assert name not in names


@pytest.mark.asyncio
async def test_volume_crud():
    vol_name = "pytest-vol"
    try:
        await MsbVolume.remove(vol_name)
    except Exception:
        pass

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post("/api/volumes", data={"name": vol_name, "kind": "dir"})
        assert r.status_code == 200
        assert r.json()["name"] == vol_name

        r = await c.get("/api/volumes")
        names = [v["name"] for v in r.json()]
        assert vol_name in names

        r = await c.delete(f"/api/volumes/{vol_name}")
        assert r.status_code == 200
        assert r.json()["status"] == "removed"


@pytest.mark.asyncio
async def test_images():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get("/api/images")
        assert r.status_code == 200
        images = r.json()
        refs = [i["reference"] for i in images]
        assert "alpine" in refs
        alpine = next((i for i in images if i["reference"] == "alpine"), None)
        assert alpine is not None
        assert alpine.get("size_bytes") is not None


@pytest.mark.asyncio
async def test_nonexistent_operations():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5) as c:
        r = await c.post("/sandboxes/nonexistent-xxx/stop")
        assert r.status_code == 200
        r = await c.post("/sandboxes/nonexistent-xxx/delete")
        assert r.status_code == 200
        r = await c.post("/sandboxes/nonexistent-xxx/kill")
        assert r.status_code == 200
        r = await c.get("/sandboxes/nonexistent-xxx")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_env_vars():
    name = "pytest-env-sb"
    await cleanup(name)
    sb = await Sandbox.create(
        name, image="alpine",
        env={"GREETING": "hello_env"}, detached=True, idle_timeout=300,
    )
    await sb.detach()
    await wait_for_status(name, "running")
    h = await Sandbox.get(name)
    s = await h.connect()
    result = await s.exec("sh", ["-c", "echo $GREETING"])
    assert "hello_env" in result.stdout_text
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_restricted_security():
    from microsandbox import SecurityProfile

    name = "pytest-res-sb"
    await cleanup(name)
    sb = await Sandbox.create(
        name, image="alpine",
        security=SecurityProfile.RESTRICTED, detached=True, idle_timeout=300,
    )
    await sb.detach()
    await wait_for_status(name, "running")
    h = await Sandbox.get(name)
    cfg = json.loads(h.config_json)
    assert cfg["security_profile"] == "restricted"
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_filesystem_ops():
    name = "pytest-fs-sb"
    await cleanup(name)
    sb = await Sandbox.create(
        name, image="alpine", detached=True, idle_timeout=300,
    )
    await sb.detach()
    await wait_for_status(name, "running")
    h = await Sandbox.get(name)
    s = await h.connect()
    await s.exec("sh", ["-c", "echo 'fs_test_data' > /tmp/test.txt"])
    content = await s.fs.read_text("/tmp/test.txt")
    assert "fs_test_data" in content
    entries = await s.fs.list("/")
    assert any(e.path == "/tmp" or e.path == "tmp" for e in entries)
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_ws_metrics():
    pytest.importorskip("websockets")
    import websockets

    name = "pytest-ws-sb"
    await cleanup(name)
    sb = await Sandbox.create(
        name, image="alpine", detached=True, idle_timeout=300,
    )
    await sb.detach()
    await wait_for_status(name, "running")
    async with websockets.connect(f"ws://127.0.0.1:8080/ws/metrics/{name}") as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(msg)
        assert "cpu" in data
    h = await Sandbox.get(name)
    await h.kill()
    await Sandbox.remove(name)


# ── Filesystem API tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fs_list():
    name = "pytest-flist-sb"
    await cleanup(name)
    sb = await Sandbox.create(name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(name, "running")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get(f"/api/sandboxes/{name}/fs/list", params={"path": "/"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert any(e["kind"] == "directory" and e["name"] == "tmp" for e in data)
    h = await Sandbox.get(name)
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_fs_write_read():
    name = "pytest-fwr-sb"
    await cleanup(name)
    sb = await Sandbox.create(name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(name, "running")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post(f"/api/sandboxes/{name}/fs/write", data={"path": "/tmp/test.txt", "content": "hello fs!"})
        assert r.status_code == 200
        assert r.json()["status"] == "written"

        r = await c.get(f"/api/sandboxes/{name}/fs/read", params={"path": "/tmp/test.txt"})
        assert r.status_code == 200
        assert r.json()["content"] == "hello fs!"
    h = await Sandbox.get(name)
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_fs_mkdir_remove():
    name = "pytest-fmr-sb"
    await cleanup(name)
    sb = await Sandbox.create(name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(name, "running")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post(f"/api/sandboxes/{name}/fs/mkdir", data={"path": "/tmp/mydir"})
        assert r.status_code == 200
        assert r.json()["status"] == "created"

        r = await c.get(f"/api/sandboxes/{name}/fs/list", params={"path": "/tmp"})
        assert any(e["name"] == "mydir" for e in r.json())

        r = await c.post(f"/api/sandboxes/{name}/fs/remove", data={"path": "/tmp/mydir"})
        assert r.status_code == 200
        assert r.json()["status"] == "removed"
    h = await Sandbox.get(name)
    await h.kill()
    await Sandbox.remove(name)


@pytest.mark.asyncio
async def test_fs_stat():
    name = "pytest-fstat-sb"
    await cleanup(name)
    sb = await Sandbox.create(name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(name, "running")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get(f"/api/sandboxes/{name}/fs/stat", params={"path": "/"})
        assert r.status_code == 200
        assert r.json()["kind"] == "directory"
    h = await Sandbox.get(name)
    await h.kill()
    await Sandbox.remove(name)


# ── Enhanced Create tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_with_env():
    name = "pytest-cenv-sb"
    await cleanup(name)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine", "cpus": 1, "memory": 256,
            "env_json": '{"MY_VAR": "hello_ui"}',
        })
        assert r.status_code == 200
        await wait_for_status(name, "running")
        r2 = await c.get(f"/api/sandboxes/{name}")
        d = r2.json()
        assert d.get("env", {}).get("MY_VAR") == "hello_ui"
    await cleanup(name)


@pytest.mark.asyncio
async def test_create_with_extra_opts():
    name = "pytest-cextra-sb"
    await cleanup(name)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.post("/sandboxes/create", data={
            "name": name, "image": "alpine", "cpus": 2, "memory": 512,
            "workdir": "/tmp", "shell": "/bin/sh",
            "idle_timeout": 600, "max_duration": 3600,
        })
        assert r.status_code == 200
        await wait_for_status(name, "running")
        r2 = await c.get(f"/api/sandboxes/{name}")
        d = r2.json()
        assert d["cpus"] == 2
        assert d["memory_mib"] == 512
    await cleanup(name)


# ── Image detail tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_image_inspect():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get("/api/images/alpine")
        assert r.status_code == 200
        d = r.json()
        assert d["reference"] == "alpine"
        assert d["architecture"] in ("amd64", "arm64", "x86_64", "aarch64")
        assert d["config"] is not None


@pytest.mark.asyncio
async def test_image_remove():
    # Pull a test image first via SDK
    sb = await Sandbox.create("pytest-img-rm", image="busybox", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status("pytest-img-rm", "running")
    await (await Sandbox.get("pytest-img-rm")).kill()
    await Sandbox.remove("pytest-img-rm")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        # Verify busybox is in the list
        r = await c.get("/api/images")
        refs = [i["reference"] for i in r.json()]
        assert "busybox" in refs

        r = await c.delete("/api/images/busybox")
        assert r.status_code == 200

        r = await c.get("/api/images")
        refs = [i["reference"] for i in r.json()]
        assert "busybox" not in refs


# ── Volume detail test ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_volume_detail():
    vname = "pytest-vdet"
    try:
        await MsbVolume.remove(vname)
    except Exception:
        pass
    v = await MsbVolume.create(name=vname, kind="dir")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get(f"/api/volumes/{vname}")
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == vname
        assert d["kind"] == "dir"

        r = await c.get(f"/api/volumes/{vname}/fs/list", params={"path": "/"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
    await MsbVolume.remove(vname)


# ── Snapshot tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_crud():
    sb_name = "pytest-snap-sb"
    await cleanup(sb_name)
    sb = await Sandbox.create(sb_name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(sb_name, "running")
    h = await Sandbox.get(sb_name)
    await h.stop(timeout=10)
    await wait_for_status(sb_name, "stopped", deadline_s=15)

    snap_name = f"test-snap-{int(datetime.now().timestamp())}"
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post("/api/snapshots", data={"source": sb_name, "name": snap_name})
        assert r.status_code == 200, f"body={r.text}"
        snap_digest = r.json()["digest"]

        r = await c.get("/api/snapshots")
        assert r.status_code == 200
        snaps = r.json()
        assert any(s["digest"] == snap_digest for s in snaps)

        r = await c.delete(f"/api/snapshots/{snap_digest}")
        assert r.status_code == 200

    await Sandbox.remove(sb_name)


# ── Image Pull test ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_image_pull():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as c:
        r = await c.post("/api/images/pull", data={"reference": "busybox"})
        assert r.status_code == 200, f"body={r.text}"
        assert r.json()["reference"] == "busybox"
        assert r.json()["status"] == "pulled"

        r = await c.get("/api/images")
        refs = [i["reference"] for i in r.json()]
        assert "busybox" in refs


# ── Snapshot Export test ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_export():
    sb_name = "pytest-export-sb"
    await cleanup(sb_name)
    sb = await Sandbox.create(sb_name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(sb_name, "running")
    h = await Sandbox.get(sb_name)
    await h.stop(timeout=10)
    await wait_for_status(sb_name, "stopped", deadline_s=15)

    snap_name = f"test-export-{int(datetime.now().timestamp())}"
    snap = await Snapshot.create(sb_name, name=snap_name)
    snap_digest = snap.digest

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as c:
        async with c.stream("GET", f"/api/snapshots/{snap_name}/export") as r:
            assert r.status_code == 200, f"export failed"
            assert "application/octet-stream" in r.headers.get("content-type", "")

    await Snapshot.remove(snap_name)
    await Sandbox.remove(sb_name)


# ── Snapshot Restore test ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_restore():
    sb_name = "pytest-restore-sb"
    await cleanup(sb_name)
    sb = await Sandbox.create(sb_name, image="alpine", detached=True, idle_timeout=300)
    await sb.detach()
    await wait_for_status(sb_name, "running")
    h = await Sandbox.get(sb_name)
    await h.stop(timeout=10)
    await wait_for_status(sb_name, "stopped", deadline_s=15)

    snap_name = f"test-restore-{int(datetime.now().timestamp())}"
    snap = await Snapshot.create(sb_name, name=snap_name)
    snap_digest = snap.digest

    restore_name = f"restored-{int(datetime.now().timestamp())}"
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as c:
        r = await c.post("/api/snapshots/restore", data={"digest": snap_digest, "name": restore_name})
        assert r.status_code == 200, f"body={r.text}"
        assert r.json()["name"] == restore_name

    await cleanup(restore_name)
    await Snapshot.remove(snap_digest)
    await Sandbox.remove(sb_name)


# ── Volume FS Write test ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_volume_fs_write():
    vname = "pytest-vfsw"
    try:
        await MsbVolume.remove(vname)
    except Exception:
        pass
    v = await MsbVolume.create(name=vname, kind="dir")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as c:
        r = await c.post(f"/api/volumes/{vname}/fs/write", data={"path": "/hello.txt", "content": "vol fs test"})
        assert r.status_code == 200, f"body={r.text}"
        assert r.json()["status"] == "written"

        r = await c.get(f"/api/volumes/{vname}/fs/read", params={"path": "/hello.txt"})
        assert r.status_code == 200
        assert r.json()["content"] == "vol fs test"

        r = await c.post(f"/api/volumes/{vname}/fs/mkdir", data={"path": "/mydir"})
        assert r.status_code == 200
        assert r.json()["status"] == "created"

        r = await c.get(f"/api/volumes/{vname}/fs/list", params={"path": "/"})
        names = [e["name"] for e in r.json()]
        assert "mydir" in names
        assert "hello.txt" in names

        # Test file removal (VolumeFs only supports remove_file, not remove_dir)
        r = await c.post(f"/api/volumes/{vname}/fs/remove", data={"path": "/hello.txt"})
        assert r.status_code == 200

    await MsbVolume.remove(vname)


# ── Sandbox Pagination test ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sandbox_pagination():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get("/api/sandboxes", params={"offset": 0, "limit": 5})
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, dict)
        assert "sandboxes" in d
        assert "total" in d
        assert "offset" in d
        assert "limit" in d

        r = await c.get("/api/sandboxes", params={"offset": 0, "limit": 1})
        d = r.json()
        assert len(d["sandboxes"]) <= 1


# ── Sandbox Search test ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sandbox_search():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
        r = await c.get("/api/sandboxes", params={"search": "nonexistent-xyz-999"})
        assert r.status_code == 200
        d = r.json()
        assert len(d["sandboxes"]) == 0

        r = await c.get("/api/sandboxes", params={"search": "alpine"})
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["sandboxes"], list)
