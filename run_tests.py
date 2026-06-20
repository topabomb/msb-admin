"""
Standalone integration test suite for msb-admin.
Runs all tests sequentially with explicit timeouts.
Usage: .venv/bin/python3 run_tests.py
"""
import asyncio
import json
import sys
import time

import httpx
from microsandbox import Sandbox, Volume as MsbVolume

BASE_URL = "http://127.0.0.1:8080"
CLIENT_TIMEOUT = 15
passed = 0
failed = 0


def client():
    return httpx.AsyncClient(base_url=BASE_URL, timeout=CLIENT_TIMEOUT)


async def cleanup(name: str):
    try:
        h = await Sandbox.get(name)
        await h.kill()
    except:
        pass
    try:
        await Sandbox.remove(name)
    except:
        pass


def ok(name: str):
    global passed
    passed += 1
    print(f"  \u2705 {name}")


def fail(name: str, msg: str):
    global failed
    failed += 1
    print(f"  \u274c {name}: {msg}")


async def test(name: str, coro):
    try:
        await asyncio.wait_for(coro, timeout=30)
        ok(name)
    except AssertionError as e:
        fail(name, str(e))
    except asyncio.TimeoutError:
        fail(name, "TIMEOUT (>30s)")
    except Exception as e:
        fail(name, f"ERROR: {e}")


async def main():
    global passed, failed

    print("\n=== Page Loads ===")
    async with client() as c:
        async def t_index():
            r = await c.get("/")
            assert r.status_code == 200, f"status={r.status_code}"
            assert "msb" in r.text.lower()

        async def t_create_form():
            r = await c.get("/create-form")
            assert r.status_code == 200
            assert "Create" in r.text

        async def t_table():
            r = await c.get("/sandboxes/table")
            assert r.status_code == 200

        await test("GET /", t_index())
        await test("GET /create-form", t_create_form())
        await test("GET /sandboxes/table", t_table())

    print("\n=== Sandbox CRUD ===")
    async with client() as c:
        async def t_list_empty():
            r = await c.get("/api/sandboxes")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

        async def t_create():
            r = await c.post("/sandboxes/create", data={
                "name": "test-py-sb", "image": "alpine",
                "cpus": "1", "memory": "256",
                "ports": "8080:80", "hostname": "testbox",
                "security": "default",
            })
            assert r.status_code == 200

        async def t_detail():
            r = await c.get("/sandboxes/test-py-sb")
            assert r.status_code == 200

        async def t_api_get():
            deadline = time.time() + 25
            while time.time() < deadline:
                r = await c.get("/api/sandboxes/test-py-sb")
                if r.status_code == 200 and r.json().get("status") == "running":
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("sandbox did not become running")
            assert r.json()["name"] == "test-py-sb"

        async def t_config():
            r = await c.get("/api/sandboxes/test-py-sb")
            d = r.json()
            assert d["cpus"] == 1
            assert d["memory_mib"] == 256

        async def t_exec():
            r = await c.post("/sandboxes/test-py-sb/exec", data={"command": "echo hello_py_test && uname -m"})
            assert r.status_code == 200
            assert "hello_py_test" in r.text
            assert "Exit:" in r.text

        async def t_stop():
            r = await c.post("/sandboxes/test-py-sb/stop")
            assert r.status_code == 200

        async def t_start():
            r = await c.post("/sandboxes/test-py-sb/start")
            assert r.status_code == 200
            deadline = time.time() + 25
            while time.time() < deadline:
                r2 = await c.get("/api/sandboxes/test-py-sb")
                if r2.json().get("status") == "running":
                    break
                await asyncio.sleep(1)

        async def t_kill():
            r = await c.post("/sandboxes/test-py-sb/kill")
            assert r.status_code == 200

        async def t_delete():
            r = await c.post("/sandboxes/test-py-sb/delete")
            assert r.status_code == 200
            await asyncio.sleep(1)
            r2 = await c.get("/api/sandboxes")
            names = [s["name"] for s in r2.json()]
            assert "test-py-sb" not in names

        await test("list empty", t_list_empty())
        await test("create sandbox", t_create())
        await test("detail page", t_detail())
        await test("API get + running", t_api_get())
        await test("config details", t_config())
        await test("exec command", t_exec())
        await test("stop", t_stop())
        await test("start", t_start())
        await test("kill", t_kill())
        await test("delete", t_delete())

    print("\n=== Volume API ===")
    async with client() as c:
        async def t_vol_create():
            try:
                await MsbVolume.remove("test-py-vol")
            except:
                pass
            r = await c.post("/api/volumes", data={"name": "test-py-vol", "kind": "dir"})
            assert r.status_code == 200
            assert r.json()["name"] == "test-py-vol"

        async def t_vol_list():
            r = await c.get("/api/volumes")
            assert r.status_code == 200
            names = [v["name"] for v in r.json()]
            assert "test-py-vol" in names

        async def t_vol_delete():
            r = await c.delete("/api/volumes/test-py-vol")
            assert r.status_code == 200
            assert r.json()["status"] == "removed"

        await test("create volume", t_vol_create())
        await test("list volumes", t_vol_list())
        await test("delete volume", t_vol_delete())

    print("\n=== Image API ===")
    async with client() as c:
        async def t_img_list():
            r = await c.get("/api/images")
            assert r.status_code == 200
            images = r.json()
            refs = [i["reference"] for i in images]
            assert "alpine" in refs

        async def t_img_schema():
            r = await c.get("/api/images")
            images = r.json()
            alpine = next((i for i in images if i["reference"] == "alpine"), None)
            assert alpine is not None
            assert alpine.get("size_bytes") is not None

        await test("list images", t_img_list())
        await test("image schema", t_img_schema())

    print("\n=== Error Handling ===")
    async with client() as c:
        async def t_stop_nonexistent():
            r = await c.post("/sandboxes/nonexistent-xxx/stop")
            assert r.status_code == 200

        async def t_delete_nonexistent():
            r = await c.post("/sandboxes/nonexistent-xxx/delete")
            assert r.status_code == 200

        async def t_kill_nonexistent():
            r = await c.post("/sandboxes/nonexistent-xxx/kill")
            assert r.status_code == 200

        await test("stop nonexistent", t_stop_nonexistent())
        await test("delete nonexistent", t_delete_nonexistent())
        await test("kill nonexistent", t_kill_nonexistent())

    print("\n=== Advanced Create (SDK direct) ===")

    async def t_env():
        await cleanup("test-env-sb")
        sb = await Sandbox.create(
            "test-env-sb", image="alpine",
            env={"GREETING": "hello_env"}, detached=True, idle_timeout=300,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            h = await Sandbox.get("test-env-sb")
            if h.status == "running":
                break
            await asyncio.sleep(0.5)
        s = await h.connect()
        result = await s.exec("sh", ["-c", "echo $GREETING"])
        assert "hello_env" in result.stdout_text
        await sb.kill()
        await Sandbox.remove("test-env-sb")

    async def t_restricted():
        from microsandbox import SecurityProfile
        await cleanup("test-res-sb")
        sb = await Sandbox.create(
            "test-res-sb", image="alpine",
            security=SecurityProfile.RESTRICTED, detached=True, idle_timeout=300,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            h = await Sandbox.get("test-res-sb")
            if h.status == "running":
                break
            await asyncio.sleep(0.5)
        h2 = await Sandbox.get("test-res-sb")
        cfg = json.loads(h2.config_json)
        assert cfg["security_profile"] == "restricted"
        await sb.kill()
        await Sandbox.remove("test-res-sb")

    async def t_fs_ops():
        await cleanup("test-fs-sb")
        sb = await Sandbox.create(
            "test-fs-sb", image="alpine", detached=True, idle_timeout=300,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            h = await Sandbox.get("test-fs-sb")
            if h.status == "running":
                break
            await asyncio.sleep(0.5)
        s = await h.connect()
        await s.exec("sh", ["-c", "echo 'fs_test_data' > /tmp/test.txt"])
        content = await s.fs.read_text("/tmp/test.txt")
        assert "fs_test_data" in content
        entries = await s.fs.list("/")
        assert any(e.path == "/tmp" or e.path == "tmp" for e in entries)
        await sb.kill()
        await Sandbox.remove("test-fs-sb")

    await test("env vars", t_env())
    await test("restricted security", t_restricted())
    await test("filesystem ops", t_fs_ops())

    print("\n=== WebSocket ===")
    try:
        import websockets

        async def t_ws_metrics():
            await cleanup("test-ws-sb")
            sb = await Sandbox.create(
                "test-ws-sb", image="alpine", detached=True, idle_timeout=300,
            )
            deadline = time.time() + 25
            while time.time() < deadline:
                h = await Sandbox.get("test-ws-sb")
                if h.status == "running":
                    break
                await asyncio.sleep(0.5)
            async with websockets.connect("ws://127.0.0.1:8080/ws/metrics/test-ws-sb") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                data = json.loads(msg)
                assert "cpu" in data
            await sb.kill()
            await Sandbox.remove("test-ws-sb")

        await test("WS metrics", t_ws_metrics())
    except ImportError:
        print("  \u23ed\ufe0f websockets not installed, skipping WS tests")

    total = passed + failed
    print(f"\n{'=' * 40}")
    print(f"  Total: {total}  Passed: {passed}  Failed: {failed}")
    if failed:
        sys.exit(1)
    else:
        print("  All tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
