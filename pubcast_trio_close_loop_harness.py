import asyncio
import os
import struct
import sys
import time
import zlib
from pathlib import Path

PKG_DIR = Path(__file__).with_name("triotestpkg_close")
if str(PKG_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PKG_DIR.parent))

from triotestpkg_close import engine


def make_voxel_payload(size: int = 8) -> bytes:
    return struct.pack("<iiII", 0, 0, 0, size) + bytes([1]) * (size ** 3)


async def run_trio_test() -> dict:
    renderer_dll = os.environ.get("PUBCAST_RENDERER_DLL")
    pcfg = engine.EngineConfig(
        engine_id="primary",
        mode=engine.EngineMode.TWIN,
        primary_port=9500,
        listen_port=9500,
        peer_port_override=9501,
        renderer_dll_path=renderer_dll,
        renderer_required=False,
    )
    ccfg = engine.EngineConfig(
        engine_id="camera_1",
        mode=engine.EngineMode.CAMERA,
        primary_port=9500,
        listen_port=9501,
        renderer_dll_path=renderer_dll,
        renderer_required=False,
    )

    primary = engine.DistributedEngineNode(pcfg)
    camera = engine.DistributedEngineNode(ccfg)
    primary.config.heartbeat_interval = 0.2
    primary.config.health_check_interval = 0.1
    camera.config.heartbeat_interval = 0.2
    camera.config.health_check_interval = 0.1

    results = {}
    payload = make_voxel_payload(8)

    await primary.start()
    await camera.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if "camera_1" in primary.get_system_status()["connected_nodes"]:
                break
            await asyncio.sleep(0.05)
        results["registered"] = "camera_1" in primary.get_system_status()["connected_nodes"]

        done = asyncio.get_running_loop().create_future()
        camera.on_work_complete(lambda work: (not done.done()) and done.set_result(work))

        work = engine.WorkUnit(
            work_id="vox_live",
            work_type="voxel_render",
            data=payload,
            metadata={"renderer_dll_path": renderer_dll} if renderer_dll else {},
        )
        primary.distribute_work(work, "camera_1")
        completed = await asyncio.wait_for(done, timeout=5.0)
        raw = zlib.decompress(completed.result)
        v_count, i_count = struct.unpack_from("<II", raw, 0)
        results["render_completed"] = completed.completed and completed.error is None
        results["vertex_count"] = v_count
        results["index_count"] = i_count
        results["rust_dll_configured"] = bool(renderer_dll)
    finally:
        await camera.stop()
        await primary.stop()
    return results


if __name__ == "__main__":
    print(asyncio.run(run_trio_test()))
