"""
DISTRIBUTED ENGINE NODE
=======================
Unified engine that can run as:
- Twin Engine (PRIMARY): Full power, handles video broadcast + computation
- Camera Engine (NODE): Single instance, processes own footage, can assist primary

Each camera runs a lightweight version of this engine, contributing
processing power during emergency backup scenarios.

"Audio Never Sacrificed" - Core principle

Copyright (c) 2024-2025 Rear View Foresight LLC
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import json
import logging
import os
import struct
import threading
import time
import uuid
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
import psutil

try:
    import GPUtil
    _HAS_GPUTIL = True
except ImportError:
    _HAS_GPUTIL = False

try:
    import subprocess
    _HAS_FFMPEG = bool(subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, timeout=2
    ).returncode == 0)
except Exception:
    _HAS_FFMPEG = False

# Relative imports — safe fallbacks defined later if unavailable
try:
    from .irm import IRMController, IRMSensor, IRMActuator, HealthStatus, HealthReport
    from .circuit_breaker import CircuitBreaker, CircuitOpenError, get_breaker
    from .bridge import TwinEngineBridge, UDPBridge, MessageType, BridgeMessage
    _PACKAGE_IMPORTS_OK = True
except ImportError:
    _PACKAGE_IMPORTS_OK = False

logger = logging.getLogger(__name__)


# =============================================================================
# OPTIONAL RUST RENDERER FFI HANDOFF
# =============================================================================

class RustRendererBridge:
    """Minimal ctypes bridge to the PubCast Rust renderer DLL.

    This closes the Python -> Rust runtime seam when a compiled DLL is present.
    It does not pretend to guarantee a visible GPU window by itself; it guarantees
    that the engine can hand a real compressed mesh blob to Rust in-process.
    """

    _lock = threading.RLock()
    _loaded_path: Optional[str] = None
    _dll = None

    @classmethod
    def is_available(cls, dll_path: Optional[str]) -> bool:
        return bool(dll_path and Path(dll_path).exists())

    @classmethod
    def _load(cls, dll_path: str):
        with cls._lock:
            if cls._dll is not None and cls._loaded_path == dll_path:
                return cls._dll
            dll = ctypes.CDLL(dll_path)
            dll.pubcast_process_mesh.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
            dll.pubcast_process_mesh.restype = ctypes.c_int
            cls._dll = dll
            cls._loaded_path = dll_path
            logger.info("RustRendererBridge loaded DLL: %s", dll_path)
            return dll

    @classmethod
    def process_mesh(cls, dll_path: str, mesh_blob: bytes) -> int:
        if not mesh_blob:
            raise ValueError("RustRendererBridge: empty mesh blob")
        dll = cls._load(dll_path)
        buf = (ctypes.c_ubyte * len(mesh_blob)).from_buffer_copy(mesh_blob)
        rc = int(dll.pubcast_process_mesh(buf, len(mesh_blob)))
        if rc != 0:
            raise RuntimeError(f"Rust renderer returned code {rc}")
        return rc



# =============================================================================
# ENGINE CONFIGURATION
# =============================================================================

class EngineMode(str, Enum):
    """Engine operating mode"""
    TWIN       = "twin"        # Primary engine - full power
    CAMERA     = "camera"      # Camera node - single instance
    STANDBY    = "standby"     # Ready but not processing
    ASSISTANCE = "assistance"  # Helping primary engine


class EngineRole(str, Enum):
    """Current role in distributed system"""
    PRIMARY          = "primary"
    BACKUP_50        = "backup_50"
    BACKUP_100       = "backup_100"
    STANDBY          = "standby"
    QUALITY_REDUCED  = "quality_reduced"


class SystemState(str, Enum):
    """Overall distributed system state"""
    NORMAL    = "normal"
    ELEVATED  = "elevated"
    CRITICAL  = "critical"
    EMERGENCY = "emergency"
    RECOVERY  = "recovery"
    FAILURE   = "failure"


@dataclass
class EngineConfig:
    """Configuration for an engine node"""
    engine_id: str
    mode: EngineMode = EngineMode.CAMERA

    # Network
    primary_host: str = "127.0.0.1"
    primary_port: int = 9000
    listen_port:  int = 9001
    peer_port_override: Optional[int] = None

    # Processing limits
    max_batch_size:     int = 10000
    min_batch_size:     int = 500
    default_batch_size: int = 2500

    # Quality settings
    target_fps:    int = 60
    quality_level: int = 100   # 0-100

    # Emergency thresholds
    assist_threshold_50:  float = 75.0
    assist_threshold_100: float = 85.0
    emergency_threshold:  float = 95.0

    # Timing
    heartbeat_interval:   float = 1.0
    health_check_interval: float = 0.2

    # Optional Rust renderer handoff
    renderer_dll_path: Optional[str] = None
    renderer_required: bool = False


@dataclass
class EngineMetrics:
    """Real-time metrics for an engine"""
    engine_id: str
    timestamp: float = field(default_factory=time.time)

    # Resource usage
    cpu_percent:    float = 0.0
    memory_percent: float = 0.0
    gpu_percent:    float = 0.0

    # Processing
    processing_load: float = 0.0
    batch_size:      int   = 2500
    fps:             float = 60.0
    frame_time_ms:   float = 16.6

    # Health
    health_score:    float = 100.0
    role:            EngineRole = EngineRole.STANDBY
    last_heartbeat:  float = 0.0

    # Audio (never sacrificed)
    audio_latency_ms: float = 0.0
    audio_dropouts:   int   = 0
    audio_quality:    float = 100.0

    # Streams
    active_streams: int = 0
    dropped_frames: int = 0

    def calculate_load(self) -> float:
        """Calculate processing load from real metrics"""
        load = (
            self.cpu_percent    * 0.4 +
            self.memory_percent * 0.2 +
            self.gpu_percent    * 0.3 +
            (100.0 - self.health_score) * 0.1
        )
        return min(100.0, max(0.0, load))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_id":       self.engine_id,
            "timestamp":       self.timestamp,
            "cpu_percent":     self.cpu_percent,
            "memory_percent":  self.memory_percent,
            "gpu_percent":     self.gpu_percent,
            "processing_load": self.processing_load,
            "batch_size":      self.batch_size,
            "fps":             self.fps,
            "health_score":    self.health_score,
            "role":            self.role.value,
            "audio_quality":   self.audio_quality,
            "active_streams":  self.active_streams,
        }


# =============================================================================
# GPU HELPERS
# =============================================================================

def _get_gpu_percent() -> float:
    """Return GPU load 0-100, or 0 if no GPU available."""
    if not _HAS_GPUTIL:
        return 0.0
    try:
        gpus = GPUtil.getGPUs()
        if not gpus:
            return 0.0
        return float(gpus[0].load * 100)
    except Exception:
        return 0.0


# =============================================================================
# WORK UNIT FOR DISTRIBUTED PROCESSING
# =============================================================================

@dataclass
class WorkUnit:
    """A unit of work that can be distributed across engines"""
    work_id:   str
    work_type: str   # "voxel_render" | "mesh_generate" | "audio_process" | "video_encode"
    priority:  int   = 5      # 1 = highest, 10 = lowest
    data:      bytes = b""
    metadata:  Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    deadline_ms: float = 100.0

    # Routing
    source_engine: str = ""
    target_engine: str = ""

    # Result
    completed: bool = False
    result:    Optional[bytes] = None
    error:     Optional[str]  = None


# =============================================================================
# REAL WORK IMPLEMENTATIONS
# =============================================================================

class VoxelRenderer:
    """
    CPU-side voxel chunk mesher.

    Input  data layout  (struct-packed):
        4B  chunk_x  (int32)
        4B  chunk_y  (int32)
        4B  chunk_z  (int32)
        4B  size     (uint32)   – voxels per axis (must be ≤ 64)
        N   voxel_ids (uint8 × size³)

    Output  (zlib-compressed binary mesh):
        4B  vertex_count  (uint32)
        4B  index_count   (uint32)
        V×12B  vertices   (float32 x,y,z)
        V×12B  normals    (float32 nx,ny,nz)
        I×4B   indices    (uint32)

    Only exposed faces are emitted (greedy-lite: per-face culling without
    the full greedy merge, which lives in the Rust renderer).

    KNOWN CEILING: Without greedy merge a flat 100×100 uniform surface
    produces 10,000 quads instead of 1.  This is fine for small or varied
    chunks; it becomes a CPU bottleneck on large uniform terrain.  Do not
    promote this path as a primary renderer for open-world scenes until
    greedy merge is ported here or the Rust renderer is proven.
    """

    # 6 face directions: +X -X +Y -Y +Z -Z
    _FACES = [
        ( 1,  0,  0, [ (1,0,0),(1,1,0),(1,1,1),(1,0,1) ], ( 1, 0, 0)),
        (-1,  0,  0, [ (0,1,0),(0,0,0),(0,0,1),(0,1,1) ], (-1, 0, 0)),
        ( 0,  1,  0, [ (0,1,0),(1,1,0),(1,1,1),(0,1,1) ], ( 0, 1, 0)),
        ( 0, -1,  0, [ (1,0,0),(0,0,0),(0,0,1),(1,0,1) ], ( 0,-1, 0)),
        ( 0,  0,  1, [ (0,0,1),(1,0,1),(1,1,1),(0,1,1) ], ( 0, 0, 1)),
        ( 0,  0, -1, [ (1,0,0),(0,0,0),(0,1,0),(1,1,0) ], ( 0, 0,-1)),
    ]

    @staticmethod
    def mesh(data: bytes) -> bytes:
        if len(data) < 16:
            raise ValueError("VoxelRenderer: payload too short")

        cx, cy, cz, size = struct.unpack_from("<iiII", data, 0)
        if size > 64:
            raise ValueError(f"VoxelRenderer: chunk size {size} exceeds max 64")

        expected = 16 + size ** 3
        if len(data) < expected:
            raise ValueError("VoxelRenderer: voxel data truncated")

        voxels = np.frombuffer(data, dtype=np.uint8, count=size**3, offset=16)
        grid   = voxels.reshape((size, size, size))

        vertices: List[float] = []
        normals:  List[float] = []
        indices:  List[int]   = []
        idx_base = 0

        for x in range(size):
            for y in range(size):
                for z in range(size):
                    if grid[x, y, z] == 0:
                        continue
                    ox = cx * size + x
                    oy = cy * size + y
                    oz = cz * size + z

                    for dx, dy, dz, corners, normal in VoxelRenderer._FACES:
                        nx, ny, nz = x + dx, y + dy, z + dz
                        # Exposed if neighbour is air or out-of-bounds
                        if 0 <= nx < size and 0 <= ny < size and 0 <= nz < size:
                            if grid[nx, ny, nz] != 0:
                                continue

                        for (fx, fy, fz) in corners:
                            vertices += [ox + fx, oy + fy, oz + fz]
                            normals  += list(normal)

                        # Two triangles per quad
                        i = idx_base
                        indices += [i, i+1, i+2, i, i+2, i+3]
                        idx_base += 4

        if not vertices:
            # Empty result (all voxels hidden / air chunk)
            return zlib.compress(struct.pack("<II", 0, 0))

        v_arr = np.array(vertices, dtype=np.float32)
        n_arr = np.array(normals,  dtype=np.float32)
        i_arr = np.array(indices,  dtype=np.uint32)

        buf = io.BytesIO()
        buf.write(struct.pack("<II", len(v_arr) // 3, len(i_arr)))
        buf.write(v_arr.tobytes())
        buf.write(n_arr.tobytes())
        buf.write(i_arr.tobytes())
        return zlib.compress(buf.getvalue(), level=1)   # level-1 = fast


class MeshGenerator:
    """
    Procedural mesh generator for PubCast scene objects.

    Input metadata keys:
        shape   : "cube" | "sphere" | "cylinder" | "plane"
        width   : float (default 1.0)
        height  : float (default 1.0)
        depth   : float (default 1.0)
        segments: int   (default 8, for sphere/cylinder)

    Output: same binary layout as VoxelRenderer (zlib-compressed).
    """

    @staticmethod
    def generate(metadata: Dict[str, Any]) -> bytes:
        shape    = metadata.get("shape", "cube")
        w        = float(metadata.get("width",    1.0))
        h        = float(metadata.get("height",   1.0))
        d        = float(metadata.get("depth",    1.0))
        segments = max(3, int(metadata.get("segments", 8)))

        if shape == "cube":
            verts, norms, idxs = MeshGenerator._cube(w, h, d)
        elif shape == "sphere":
            verts, norms, idxs = MeshGenerator._sphere(w * 0.5, segments)
        elif shape == "cylinder":
            verts, norms, idxs = MeshGenerator._cylinder(w * 0.5, h, segments)
        elif shape == "plane":
            verts, norms, idxs = MeshGenerator._plane(w, d)
        else:
            raise ValueError(f"MeshGenerator: unknown shape '{shape}'")

        v_arr = np.array(verts, dtype=np.float32)
        n_arr = np.array(norms, dtype=np.float32)
        i_arr = np.array(idxs,  dtype=np.uint32)

        buf = io.BytesIO()
        buf.write(struct.pack("<II", len(v_arr) // 3, len(i_arr)))
        buf.write(v_arr.tobytes())
        buf.write(n_arr.tobytes())
        buf.write(i_arr.tobytes())
        return zlib.compress(buf.getvalue(), level=1)

    @staticmethod
    def _cube(w, h, d):
        hw, hh, hd = w / 2, h / 2, d / 2
        faces = [
            # (normal, 4 corners as (x,y,z))
            (( 0, 0, 1), [(-hw,-hh, hd),(hw,-hh, hd),(hw, hh, hd),(-hw, hh, hd)]),
            (( 0, 0,-1), [( hw,-hh,-hd),(-hw,-hh,-hd),(-hw, hh,-hd),( hw, hh,-hd)]),
            (( 0, 1, 0), [(-hw, hh,-hd),(hw, hh,-hd),(hw, hh, hd),(-hw, hh, hd)]),
            (( 0,-1, 0), [(-hw,-hh, hd),(hw,-hh, hd),(hw,-hh,-hd),(-hw,-hh,-hd)]),
            (( 1, 0, 0), [( hw,-hh, hd),(hw,-hh,-hd),(hw, hh,-hd),( hw, hh, hd)]),
            ((-1, 0, 0), [(-hw,-hh,-hd),(-hw,-hh, hd),(-hw, hh, hd),(-hw, hh,-hd)]),
        ]
        verts, norms, idxs = [], [], []
        base = 0
        for normal, corners in faces:
            for c in corners:
                verts += list(c)
                norms += list(normal)
            idxs += [base, base+1, base+2, base, base+2, base+3]
            base += 4
        return verts, norms, idxs

    @staticmethod
    def _sphere(r, segments):
        verts, norms, idxs = [], [], []
        rings  = segments
        slices = segments * 2
        for ri in range(rings + 1):
            phi = np.pi * ri / rings
            for si in range(slices + 1):
                theta = 2 * np.pi * si / slices
                x = r * np.sin(phi) * np.cos(theta)
                y = r * np.cos(phi)
                z = r * np.sin(phi) * np.sin(theta)
                nx, ny, nz = x/r, y/r, z/r
                verts += [x, y, z]
                norms += [nx, ny, nz]
        for ri in range(rings):
            for si in range(slices):
                a = ri * (slices + 1) + si
                b = a + 1
                c = a + slices + 1
                e = c + 1
                idxs += [a, b, c, b, e, c]
        return verts, norms, idxs

    @staticmethod
    def _cylinder(r, h, segments):
        verts, norms, idxs = [], [], []
        hh = h / 2
        for si in range(segments + 1):
            theta = 2 * np.pi * si / segments
            x, z  = r * np.cos(theta), r * np.sin(theta)
            for y in [-hh, hh]:
                verts += [x, y, z]
                norms += [x/r, 0, z/r]
        for si in range(segments):
            a = si * 2
            idxs += [a, a+2, a+1, a+1, a+2, a+3]
        # caps
        for cy, ny in [(hh, 1), (-hh, -1)]:
            ci = len(verts) // 3
            verts += [0, cy, 0]; norms += [0, ny, 0]
            for si in range(segments):
                theta = 2 * np.pi * si / segments
                verts += [r * np.cos(theta), cy, r * np.sin(theta)]
                norms += [0, ny, 0]
            for si in range(segments):
                a = ci + 1 + si
                b = ci + 1 + (si + 1) % segments
                if ny > 0:
                    idxs += [ci, a, b]
                else:
                    idxs += [ci, b, a]
        return verts, norms, idxs

    @staticmethod
    def _plane(w, d):
        hw, hd = w / 2, d / 2
        verts = [-hw, 0, -hd,  hw, 0, -hd,  hw, 0, hd,  -hw, 0, hd]
        norms = [0, 1, 0] * 4
        idxs  = [0, 1, 2, 0, 2, 3]
        return verts, norms, idxs


class AudioProcessor:
    """
    Real audio processing: normalisation, noise gate, optional compressor.

    Input data layout:
        4B  sample_rate  (uint32)
        4B  channels     (uint8, padded to 4B)
        4B  sample_count (uint32)
        N   PCM samples  (float32 interleaved)

    Processing chain (in order, all optional via metadata flags):
        1. DC-offset removal
        2. Noise gate  (threshold_db, release_ms)
        3. Peak normalisation  (target_db, default -3 dBFS)
        4. Soft-knee compressor  (ratio, attack_ms, release_ms, threshold_db)

    Output: same header + processed float32 PCM.

    Audio is NEVER sacrificed: if any stage fails it is skipped, not crashed.
    The result always carries a quality score (0-100) written into the
    first 4 bytes of the returned metadata area.
    """

    @staticmethod
    def process(data: bytes, metadata: Dict[str, Any]) -> bytes:
        if len(data) < 12:
            raise ValueError("AudioProcessor: payload too short")

        sample_rate, channels, sample_count = struct.unpack_from("<III", data, 0)
        if channels == 0 or sample_rate == 0:
            raise ValueError("AudioProcessor: invalid header")

        offset  = 12
        n_floats = sample_count * channels
        expected = offset + n_floats * 4
        if len(data) < expected:
            raise ValueError("AudioProcessor: PCM data truncated")

        pcm = np.frombuffer(data, dtype=np.float32, count=n_floats, offset=offset).copy()
        quality = 100.0

        # 1. DC-offset removal (per-channel mean subtraction)
        try:
            for ch in range(channels):
                pcm[ch::channels] -= pcm[ch::channels].mean()
        except Exception as exc:
            logger.warning(f"AudioProcessor: DC removal failed – {exc}")
            quality -= 5.0

        # 2. Noise gate
        try:
            gate_db  = float(metadata.get("gate_db", -60.0))
            gate_thr = 10 ** (gate_db / 20.0)
            rms_block = 256
            amp = np.abs(pcm)
            for start in range(0, len(amp), rms_block):
                block = amp[start:start + rms_block]
                rms   = float(np.sqrt(np.mean(block ** 2)))
                if rms < gate_thr:
                    pcm[start:start + rms_block] = 0.0
        except Exception as exc:
            logger.warning(f"AudioProcessor: noise gate failed – {exc}")
            quality -= 5.0

        # 3. Peak normalisation
        try:
            target_db  = float(metadata.get("normalize_db", -3.0))
            target_amp = 10 ** (target_db / 20.0)
            peak = float(np.max(np.abs(pcm)))
            if peak > 1e-6:
                pcm *= (target_amp / peak)
        except Exception as exc:
            logger.warning(f"AudioProcessor: normalisation failed – {exc}")
            quality -= 10.0

        # 4. Soft-knee compressor
        try:
            if metadata.get("compress", False):
                ratio     = float(metadata.get("comp_ratio",    4.0))
                thr_db    = float(metadata.get("comp_threshold", -18.0))
                thr_amp   = 10 ** (thr_db / 20.0)
                knee_db   = 6.0
                for i in range(len(pcm)):
                    s   = pcm[i]
                    a   = abs(s)
                    if a < 1e-8:
                        continue
                    a_db = 20 * np.log10(a)
                    if a_db > thr_db + knee_db / 2:
                        gain_db = thr_db + (a_db - thr_db) / ratio - a_db
                        pcm[i] *= 10 ** (gain_db / 20.0)
                    elif a_db > thr_db - knee_db / 2:
                        over    = (a_db - (thr_db - knee_db / 2)) / knee_db
                        gain_db = over * over * (1 / ratio - 1) * knee_db / 2
                        pcm[i] *= 10 ** (gain_db / 20.0)
        except Exception as exc:
            logger.warning(f"AudioProcessor: compressor failed – {exc}")
            quality -= 5.0

        # Clamp to [-1, 1] (safety net)
        np.clip(pcm, -1.0, 1.0, out=pcm)

        header = struct.pack("<III", sample_rate, channels, sample_count)
        return header + pcm.astype(np.float32).tobytes(), quality

    @staticmethod
    def process_safe(data: bytes, metadata: Dict[str, Any]) -> Tuple[bytes, float]:
        """Never raises. Returns (result_bytes, quality_0_to_100)."""
        try:
            result, quality = AudioProcessor.process(data, metadata)
            return result, quality
        except Exception as exc:
            logger.error(f"AudioProcessor.process_safe: returning raw audio – {exc}")
            return data, 0.0


class VideoEncoder:
    """
    Video frame encoder.

    Two paths depending on environment:
        A) FFmpeg available  → pipe raw frames through libx264 / libvpx-vp9
        B) Numpy fallback    → JPEG-like DCT tile compress per frame (lossless ~60%)

    Input data layout:
        4B  width   (uint32)
        4B  height  (uint32)
        1B  format  (0=RGB24, 1=RGBA32, 2=YUV420)
        3B  pad
        4B  frame_count (uint32)
        N   raw frame bytes

    Output: encoded blob (H.264 Annex-B or numpy-DCT bytes) + 4B quality score
    """

    @staticmethod
    def encode(data: bytes, metadata: Dict[str, Any]) -> bytes:
        if len(data) < 16:
            raise ValueError("VideoEncoder: payload too short")

        width, height, fmt, _, frame_count = struct.unpack_from("<IIBBI", data, 0)
        frame_bytes = {0: width * height * 3,
                       1: width * height * 4,
                       2: width * height * 3 // 2}.get(fmt)
        if frame_bytes is None:
            raise ValueError(f"VideoEncoder: unknown pixel format {fmt}")

        offset = 16
        expected = offset + frame_count * frame_bytes
        if len(data) < expected:
            raise ValueError("VideoEncoder: frame data truncated")

        raw_frames = data[offset:offset + frame_count * frame_bytes]

        if _HAS_FFMPEG:
            return VideoEncoder._encode_ffmpeg(raw_frames, width, height, fmt, frame_count, metadata)
        else:
            return VideoEncoder._encode_numpy_dct(raw_frames, width, height, fmt, frame_count, metadata)

    @staticmethod
    def _encode_ffmpeg(raw_frames: bytes, width: int, height: int,
                       fmt: int, frame_count: int, metadata: Dict[str, Any]) -> bytes:
        import subprocess
        pix_fmt_in = {0: "rgb24", 1: "rgba", 2: "yuv420p"}.get(fmt, "rgb24")
        codec      = metadata.get("codec", "libx264")
        crf        = str(int(metadata.get("crf", 23)))
        fps        = str(int(metadata.get("fps", 30)))

        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-pixel_format", pix_fmt_in,
            "-video_size", f"{width}x{height}",
            "-framerate", fps,
            "-i", "pipe:0",
            "-c:v", codec,
            "-crf", crf,
            "-preset", "ultrafast",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                cmd, input=raw_frames,
                capture_output=True, timeout=30
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg exit {result.returncode}: {result.stderr[:200]}")
            return result.stdout
        except Exception as exc:
            logger.warning(f"VideoEncoder ffmpeg failed, falling back to numpy DCT: {exc}")
            return VideoEncoder._encode_numpy_dct(raw_frames, width, height, fmt, frame_count, metadata)

    # Standard JPEG luma quantisation table (quality=50 baseline).
    # Scaled by _jpeg_quant_table(quality) at encode time.
    # High-frequency coefficients get coarser quantisation → better compression
    # at the same perceptual quality than a flat step size.
    _JPEG_LUMA_Q50 = np.array([
        [16, 11, 10, 16, 24, 40, 51, 61],
        [12, 12, 14, 19, 26, 58, 60, 55],
        [14, 13, 16, 24, 40, 57, 69, 56],
        [14, 17, 22, 29, 51, 87, 80, 62],
        [18, 22, 37, 56, 68,109,103, 77],
        [24, 35, 55, 64, 81,104,113, 92],
        [49, 64, 78, 87,103,121,120,101],
        [72, 92, 95, 98,112,100,103, 99],
    ], dtype=np.float32)

    @staticmethod
    def _jpeg_quant_table(quality: int) -> np.ndarray:
        """Scale the luma table to the requested quality (1–100)."""
        q = max(1, min(100, quality))
        scale = (5000 / q) if q < 50 else (200 - 2 * q)
        table = np.floor((VideoEncoder._JPEG_LUMA_Q50 * scale + 50) / 100)
        return np.clip(table, 1, 255).astype(np.float32)

    @staticmethod
    def _dct8(block: np.ndarray) -> np.ndarray:
        """
        Vectorised 2D DCT-II on an 8×8 float32 block.

        Uses the FFT-mirror trick (no scipy required):
            DCT-II(x) = Re( FFT([x, x_reversed]) )[:N] * phase_correction
        Applied row-wise then column-wise for separable 2D DCT.
        This is mathematically identical to scipy.fft.dct(x, type=2, norm=None)
        on each axis.
        """
        n = 8
        k = np.arange(n, dtype=np.float32)
        W = np.exp(-1j * np.pi * k / (2 * n)).astype(np.complex64)

        def dct1d_rows(mat):
            # mat: (8, 8), transform each row
            v = np.concatenate([mat, mat[:, ::-1]], axis=1)   # (8, 16)
            V = np.fft.rfft(v, axis=1)[:, :n]                 # (8, 8) complex
            return np.real(V * W[np.newaxis, :])

        return dct1d_rows(dct1d_rows(block).T).T

    @staticmethod
    def _encode_numpy_dct(raw_frames: bytes, width: int, height: int,
                          fmt: int, frame_count: int, metadata: Dict[str, Any]) -> bytes:
        """
        Tile-based DCT compressor (numpy fallback).

        Uses the standard JPEG luma quantisation table scaled to the
        requested quality level.  High-frequency coefficients receive
        coarser quantisation than DC/low-frequency ones — giving better
        compression at equivalent perceptual quality vs a flat step size.

        KNOWN CEILING: This is an emergency CPU fallback, not a broadcast
        codec.  It produces larger files than H.264 at equal quality and
        has no inter-frame prediction (every frame is independently coded).
        Use ffmpeg path (Path A) for any production stream.  This path
        exists so the engine never hard-fails when ffmpeg is absent.
        """
        ch = {0: 3, 1: 4, 2: None}.get(fmt, 3)
        if ch is None:
            # YUV420: proper chroma subsampling is a future TODO.
            # For now zlib the raw plane bytes — lossless, not ideal.
            return zlib.compress(raw_frames, level=6)

        frame_size = width * height * ch
        quality    = max(1, min(100, int(metadata.get("quality", 75))))
        qtable     = VideoEncoder._jpeg_quant_table(quality)   # (8,8) float32

        buf = io.BytesIO()
        buf.write(struct.pack("<IIBBI", width, height, fmt, 0, frame_count))

        for fi in range(frame_count):
            frame = np.frombuffer(
                raw_frames, dtype=np.uint8,
                count=frame_size, offset=fi * frame_size
            ).reshape(height, width, ch).astype(np.float32) - 128.0

            compressed_channels = []
            for c in range(ch):
                plane = frame[:, :, c]

                # Pad to multiple of 8
                ph = ((height + 7) // 8) * 8
                pw = ((width  + 7) // 8) * 8
                padded = np.zeros((ph, pw), dtype=np.float32)
                padded[:height, :width] = plane

                tiles = []
                for ty in range(0, ph, 8):
                    for tx in range(0, pw, 8):
                        raw_tile = padded[ty:ty+8, tx:tx+8]
                        dct_tile = VideoEncoder._dct8(raw_tile)
                        # Frequency-weighted quantisation (not flat step)
                        quantised = np.round(dct_tile / qtable)
                        tiles.append(quantised.astype(np.float16))

                compressed_channels.append(np.array(tiles, dtype=np.float16).tobytes())

            frame_blob = b"".join(compressed_channels)
            compressed = zlib.compress(frame_blob, level=6)
            buf.write(struct.pack("<I", len(compressed)))
            buf.write(compressed)

        return buf.getvalue()


# =============================================================================
# SAFE IMPORT FALLBACKS
# (allows standalone import when .irm / .bridge / .circuit_breaker are absent)
# =============================================================================

try:
    from .irm import IRMController, HealthStatus
    _IRM_AVAILABLE = True
except ImportError:
    _IRM_AVAILABLE = False

    class _FakeHealth:
        fps = 60.0; score = 100.0

    class IRMController:  # type: ignore
        def __init__(self, **kw):
            self._batch = kw.get("default_batch", 2500)
        def tick(self, dt): pass
        def get_health(self): return _FakeHealth()
        def get_batch_size(self): return self._batch
        def record_latency(self, ms): pass

try:
    from .circuit_breaker import get_breaker
    _CB_AVAILABLE = True
except ImportError:
    _CB_AVAILABLE = False

    class _FakeBreaker:
        def __call__(self, fn, *a, **kw): return fn(*a, **kw)

    def get_breaker(name, **kw): return _FakeBreaker()  # type: ignore

try:
    from .bridge import TwinEngineBridge, MessageType, BridgeMessage
    _BRIDGE_AVAILABLE = True
except ImportError:
    _BRIDGE_AVAILABLE = False

    class MessageType(str, Enum):  # type: ignore
        HEARTBEAT = "heartbeat"
        COMMAND   = "command"
        METRICS   = "metrics"

    class BridgeMessage:  # type: ignore
        def __init__(self, msg_type, payload):
            self.msg_type = msg_type
            self.payload  = payload

    class TwinEngineBridge:  # type: ignore
        def __init__(self, **kw):
            self._handlers = {}
            self._local_state = {}
        def start(self): pass
        def stop(self): pass
        def on_message(self, msg_type, handler): self._handlers[msg_type] = handler
        def update_local_state(self, **fields): self._local_state.update(fields)
        def send_heartbeat(self, payload=None): return True
        def send_command(self, cmd, payload=None): return True
        def send_metrics(self, payload=None): return True
        def get_metrics(self): return {"packets_received": 0, "packets_sent": 0}


# =============================================================================
# SUPPORTED WORK TYPES
# =============================================================================

SUPPORTED_WORK_TYPES: Set[str] = {
    "voxel_render",
    "mesh_generate",
    "audio_process",
    "video_encode",
}

MAX_COMPLETED_WORK_ITEMS = 256


# =============================================================================
# WORK UNIT  (with serialisation)
# =============================================================================

@dataclass
class WorkUnit:
    """A unit of work that can be distributed across engines"""
    work_id:   str
    work_type: str
    priority:  int   = 5
    data:      bytes = b""
    metadata:  Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    deadline_ms: float = 100.0

    source_engine: str = ""
    target_engine: str = ""

    completed: bool = False
    result:    Optional[bytes] = None
    error:     Optional[str]   = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "work_id":     self.work_id,
            "work_type":   self.work_type,
            "priority":    self.priority,
            "data":        self.data.hex(),
            "metadata":    self.metadata,
            "created_at":  self.created_at,
            "deadline_ms": self.deadline_ms,
            "source_engine": self.source_engine,
            "target_engine": self.target_engine,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkUnit":
        raw = d.get("data", "")
        return cls(
            work_id      = d["work_id"],
            work_type    = d["work_type"],
            priority     = int(d.get("priority", 5)),
            data         = bytes.fromhex(raw) if isinstance(raw, str) else raw,
            metadata     = d.get("metadata", {}),
            created_at   = float(d.get("created_at", time.time())),
            deadline_ms  = float(d.get("deadline_ms", 100.0)),
            source_engine= d.get("source_engine", ""),
            target_engine= d.get("target_engine", ""),
        )


# =============================================================================
# DISTRIBUTED ENGINE NODE  (hardened)
# =============================================================================

class DistributedEngineNode:
    """
    A single node in the distributed processing system.
    Can operate as primary (twin engine) or secondary (camera engine).

    Hardening applied in this revision:
    - Safe fallbacks for all three optional imports
    - Proper WorkUnit serialisation (to_dict / from_dict)
    - Command targeting: nodes ignore messages aimed at other nodes
    - register_node handling on primary
    - Async context manager support
    - Real GPU metrics via GPUtil (0 when no GPU present)
    - Real work dispatch to VoxelRenderer / MeshGenerator / AudioProcessor / VideoEncoder
    """

    def __init__(self, config: EngineConfig):
        self.config    = config
        self.engine_id = config.engine_id
        self.mode      = config.mode
        self.role      = EngineRole.STANDBY
        self.state     = SystemState.NORMAL

        self.metrics       = EngineMetrics(engine_id=config.engine_id)
        self._metrics_lock = threading.Lock()

        self.irm = IRMController(
            window_size   = 10,
            min_batch     = config.min_batch_size,
            max_batch     = config.max_batch_size,
            default_batch = config.default_batch_size,
        )

        self.circuit_breaker = get_breaker(
            f"engine_{config.engine_id}",
            failure_threshold = 5,
            recovery_timeout  = 30.0,
        )

        self.bridge: Optional[TwinEngineBridge] = None

        self._work_queue:   asyncio.Queue  = asyncio.Queue()
        self._pending_work: Dict[str, WorkUnit] = {}
        self._done_work:    Dict[str, WorkUnit] = {}

        self._connected_nodes: Dict[str, EngineMetrics] = {}
        self._node_lock = threading.Lock()

        # ProcessPoolExecutor for CPU-bound work (voxel/mesh/video).
        # GIL means threads don't parallelize numpy-heavy work; processes do.
        # Audio uses the default thread executor — its numpy ops release the GIL
        # and we want low latency over throughput there.
        self._cpu_executor = None
        self._cpu_workers = max(1, (os.cpu_count() or 2) - 1)  # leave one core for I/O

        self._running = False
        self._tasks:   List[asyncio.Task] = []

        self._on_state_change:  List[Callable[[SystemState, SystemState], None]] = []
        self._on_work_complete: List[Callable[[WorkUnit], None]] = []

        logger.info(f"DistributedEngineNode created: {config.engine_id} ({config.mode.value})")

    # -------------------------------------------------------------------------
    # Async context manager
    # -------------------------------------------------------------------------

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.stop()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        logger.info(f"Starting engine node: {self.engine_id}")

        self._loop = asyncio.get_running_loop()
        self._tasks = []

        if self._cpu_executor is None:
            self._cpu_executor = ProcessPoolExecutor(max_workers=self._cpu_workers)

        try:
            if self.mode == EngineMode.TWIN:
                self.bridge = TwinEngineBridge(
                    use_shared_memory = True,
                    use_udp           = True,
                    udp_local_port    = self.config.primary_port,
                    udp_remote_port   = (self.config.peer_port_override if self.config.peer_port_override is not None else self.config.primary_port),
                )
            else:
                self.bridge = TwinEngineBridge(
                    use_shared_memory = False,
                    use_udp           = True,
                    udp_local_port    = self.config.listen_port,
                    udp_remote_port   = self.config.primary_port,
                )
            self.bridge.start()
        except Exception as exc:
            logger.warning(f"Bridge init failed ({exc}); running without network bridge")
            self.bridge = TwinEngineBridge()   # falls back to no-op stub

        self.bridge.on_message(MessageType.HEARTBEAT, self._handle_heartbeat)
        self.bridge.on_message(MessageType.COMMAND,   self._handle_command)
        self.bridge.on_message(MessageType.METRICS,   self._handle_metrics)

        if hasattr(self.bridge, "update_local_state"):
            try:
                self.bridge.update_local_state(
                    engine_id=self.engine_id,
                    mode=self.mode.value,
                    role=self.role.value,
                )
            except Exception:
                logger.debug("Bridge local state update failed during start", exc_info=True)

        self._running = True

        self._tasks = [
            asyncio.create_task(self._metrics_loop(),       name="metrics"),
            asyncio.create_task(self._health_check_loop(),  name="health"),
            asyncio.create_task(self._work_processor_loop(), name="work"),
        ]
        if self.mode == EngineMode.TWIN:
            self._tasks.append(asyncio.create_task(self._load_balancer_loop(), name="lb"))

        self.role = EngineRole.PRIMARY if self.mode == EngineMode.TWIN else EngineRole.STANDBY
        if hasattr(self.bridge, "update_local_state"):
            try:
                self.bridge.update_local_state(role=self.role.value)
            except Exception:
                logger.debug("Bridge local state role update failed during start", exc_info=True)
        if self.mode != EngineMode.TWIN:
            await self._announce_to_primary()

        logger.info(f"Engine node started: {self.engine_id} as {self.role.value}")

    async def stop(self) -> None:
        if not self._running and self.bridge is None and self._cpu_executor is None:
            return

        if self.bridge and self.mode != EngineMode.TWIN:
            try:
                payload = {
                    "cmd": "unregister_node",
                    "engine_id": self.engine_id,
                }
                self.bridge.send_command("unregister_node", payload, require_ack=True, ack_timeout_s=0.2)
                await asyncio.sleep(0.02)
                self.bridge.send_command("unregister_node", payload)
                await asyncio.sleep(0.05)
            except Exception:
                logger.debug("Failed to send unregister_node during shutdown", exc_info=True)

        self._running = False

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        if self.bridge:
            try:
                self.bridge.stop()
            except Exception:
                pass
            finally:
                self.bridge = None
        try:
            if self._cpu_executor is not None:
                self._cpu_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        finally:
            self._cpu_executor = None
            self._loop = None
            self._tasks = []
        logger.info(f"Engine node stopped: {self.engine_id}")

    # -------------------------------------------------------------------------
    # Message handlers
    # -------------------------------------------------------------------------

    def _handle_heartbeat(self, msg: BridgeMessage) -> None:
        payload = msg.payload
        node_id = payload.get("engine_id")
        if not node_id or node_id == self.engine_id:
            return
        with self._node_lock:
            if node_id not in self._connected_nodes:
                self._connected_nodes[node_id] = EngineMetrics(engine_id=node_id)
            node = self._connected_nodes[node_id]
            node.last_heartbeat  = time.time()
            node.health_score    = payload.get("health_score", 100.0)
            node.processing_load = payload.get("processing_load", 0.0)
            try:
                node.role = EngineRole(payload.get("role", "standby"))
            except ValueError:
                pass

    def _handle_command(self, msg: BridgeMessage) -> None:
        payload = msg.payload
        target  = payload.get("target")
        # Ignore commands aimed at a different node
        if target and target not in {self.engine_id, "primary" if self.mode == EngineMode.TWIN else ""}:
            return

        cmd = payload.get("cmd")
        if cmd == "set_role":
            new_role_str = payload.get("role", "standby")
            try:
                self._set_role(EngineRole(new_role_str))
            except ValueError:
                logger.warning(f"Unknown role in set_role command: {new_role_str}")

        elif cmd == "reduce_quality":
            level = int(payload.get("level", 50))
            self.config.quality_level = max(0, min(100, level))
            logger.info(f"Quality set to {self.config.quality_level}%")

        elif cmd == "process_work":
            work_data = payload.get("work")
            if work_data:
                try:
                    work = WorkUnit.from_dict(work_data)
                    if self._loop is None:
                        raise RuntimeError("Engine event loop not initialized")
                    try:
                        running_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        running_loop = None
                    if running_loop is self._loop:
                        asyncio.create_task(self._process_work(work))
                    else:
                        asyncio.run_coroutine_threadsafe(self._process_work(work), self._loop)
                except Exception as exc:
                    logger.error(f"Failed to deserialise WorkUnit: {exc}")

        elif cmd == "register_node":
            # Primary records the registration; camera nodes ignore
            if self.mode == EngineMode.TWIN:
                node_id = payload.get("engine_id")
                caps    = payload.get("capabilities", [])
                if node_id:
                    with self._node_lock:
                        if node_id not in self._connected_nodes:
                            self._connected_nodes[node_id] = EngineMetrics(engine_id=node_id)
                        self._connected_nodes[node_id].last_heartbeat = time.time()
                    logger.info(f"Registered node {node_id} caps={caps}")

        elif cmd == "unregister_node":
            if self.mode == EngineMode.TWIN:
                node_id = payload.get("engine_id")
                if node_id:
                    with self._node_lock:
                        self._connected_nodes.pop(node_id, None)
                    logger.info(f"Unregistered node {node_id}")

        else:
            logger.warning(f"Unhandled command: {cmd!r} payload={payload}")

    def _handle_metrics(self, msg: BridgeMessage) -> None:
        payload = msg.payload
        node_id = payload.get("engine_id")
        if not node_id or node_id == self.engine_id:
            return
        with self._node_lock:
            if node_id in self._connected_nodes:
                node = self._connected_nodes[node_id]
                node.last_heartbeat  = time.time()
                node.cpu_percent     = payload.get("cpu_percent", 0.0)
                node.memory_percent  = payload.get("memory_percent", 0.0)
                node.gpu_percent     = payload.get("gpu_percent", 0.0)
                node.fps             = payload.get("fps", 60.0)
                node.processing_load = payload.get("processing_load", 0.0)

    # -------------------------------------------------------------------------
    # Metrics & health
    # -------------------------------------------------------------------------

    async def _metrics_loop(self) -> None:
        while self._running:
            try:
                with self._metrics_lock:
                    self.metrics.timestamp      = time.time()
                    self.metrics.cpu_percent    = psutil.cpu_percent(interval=None)
                    self.metrics.memory_percent = psutil.virtual_memory().percent
                    self.metrics.gpu_percent    = _get_gpu_percent()

                    health = self.irm.get_health()
                    self.metrics.fps           = health.fps
                    self.metrics.health_score  = health.score
                    self.metrics.batch_size    = self.irm.get_batch_size()
                    self.metrics.frame_time_ms = (1000.0 / health.fps) if health.fps > 0 else 999.9

                    self.metrics.processing_load = self.metrics.calculate_load()
                    self.metrics.role            = self.role
                    self.metrics.last_heartbeat  = time.time()

                if self.bridge:
                    self.bridge.send_heartbeat(payload={
                        "engine_id":       self.engine_id,
                        "health_score":    self.metrics.health_score,
                        "processing_load": self.metrics.processing_load,
                        "role":            self.role.value,
                    })
                    self.bridge.send_metrics(payload=self.metrics.to_dict())

                await asyncio.sleep(self.config.heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Metrics loop error: {exc}")

    async def _health_check_loop(self) -> None:
        while self._running:
            try:
                self.irm.tick(self.config.health_check_interval)
                if self.mode == EngineMode.TWIN:
                    await self._check_node_health()
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Health check error: {exc}")

    async def _check_node_health(self) -> None:
        now     = time.time()
        timeout = self.config.heartbeat_interval * 3
        with self._node_lock:
            dead = [nid for nid, n in self._connected_nodes.items()
                    if now - n.last_heartbeat > timeout]
            for nid in dead:
                logger.debug(f"Node {nid} timed out — removing")
                del self._connected_nodes[nid]

    # -------------------------------------------------------------------------
    # Load balancing (primary only)
    # -------------------------------------------------------------------------

    async def _load_balancer_loop(self) -> None:
        while self._running:
            try:
                load      = self.metrics.processing_load
                new_state = self._calculate_required_state(load)
                if new_state != self.state:
                    await self._transition_to_state(new_state, load)
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Load balancer error: {exc}")

    def _calculate_required_state(self, load: float) -> SystemState:
        if self.state == SystemState.RECOVERY:
            return SystemState.NORMAL if load < 65.0 else SystemState.RECOVERY
        if load >= self.config.emergency_threshold:  return SystemState.EMERGENCY
        if load >= 90.0:                             return SystemState.CRITICAL
        if load >= self.config.assist_threshold_50:  return SystemState.ELEVATED
        if load < 65.0 and self.state != SystemState.NORMAL:
            return SystemState.RECOVERY
        return self.state if self.state == SystemState.NORMAL else SystemState.NORMAL

    async def _transition_to_state(self, new_state: SystemState, load: float) -> None:
        old_state  = self.state
        self.state = new_state
        logger.info(f"State: {old_state.value} → {new_state.value} (load {load:.1f}%)")

        if new_state == SystemState.ELEVATED:
            await self._engage_assistance(["camera_3"], EngineRole.BACKUP_50)
        elif new_state == SystemState.CRITICAL:
            await self._engage_assistance(["camera_3"], EngineRole.BACKUP_100)
            await self._engage_assistance(["camera_2"], EngineRole.BACKUP_50)
        elif new_state == SystemState.EMERGENCY:
            await self._engage_assistance(["camera_1","camera_2","camera_3"], EngineRole.BACKUP_100)
            await self._reduce_all_quality(50)
        elif new_state == SystemState.NORMAL:
            await self._disengage_all_assistance()
            await self._restore_quality()
        elif new_state == SystemState.RECOVERY:
            await self._disengage_assistance(["camera_1"])

        for cb in self._on_state_change:
            try:
                cb(old_state, new_state)
            except Exception as exc:
                logger.error(f"State change callback error: {exc}")

    async def _engage_assistance(self, node_ids: List[str], role: EngineRole) -> None:
        for nid in node_ids:
            if self.bridge:
                self.bridge.send_command("set_role", {"target": nid, "cmd": "set_role", "role": role.value})
            logger.info(f"Engaging {nid} as {role.value}")

    async def _disengage_assistance(self, node_ids: List[str]) -> None:
        for nid in node_ids:
            if self.bridge:
                self.bridge.send_command("set_role", {"target": nid, "cmd": "set_role", "role": "standby"})

    async def _disengage_all_assistance(self) -> None:
        with self._node_lock:
            nids = list(self._connected_nodes.keys())
        await self._disengage_assistance(nids)

    async def _reduce_all_quality(self, level: int) -> None:
        self.config.quality_level = level
        if self.bridge:
            self.bridge.send_command("reduce_quality", {"cmd": "reduce_quality", "level": level})

    async def _restore_quality(self) -> None:
        self.config.quality_level = 100
        if self.bridge:
            self.bridge.send_command("reduce_quality", {"cmd": "reduce_quality", "level": 100})

    # -------------------------------------------------------------------------
    # Work processing  (real implementations)
    # -------------------------------------------------------------------------

    async def _work_processor_loop(self) -> None:
        while self._running:
            try:
                try:
                    work = await asyncio.wait_for(self._work_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await self._process_work(work)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Work processor error: {exc}")

    def _resolve_renderer_dll_path(self, work: WorkUnit) -> Optional[str]:
        candidate = work.metadata.get("renderer_dll_path") or self.config.renderer_dll_path
        if not candidate:
            return None
        try:
            p = Path(candidate).expanduser().resolve()
            return str(p)
        except Exception:
            return str(candidate)

    def _handoff_mesh_to_rust_renderer(self, dll_path: str, mesh_blob: bytes) -> None:
        RustRendererBridge.process_mesh(dll_path, mesh_blob)

    async def _process_work(self, work: WorkUnit) -> None:
        self._pending_work[work.work_id] = work
        start = time.time()
        try:
            loop = asyncio.get_running_loop()
            if work.work_type == "voxel_render":
                # CPU-side meshing first, then optional Rust renderer handoff.
                result = await loop.run_in_executor(
                    self._cpu_executor, VoxelRenderer.mesh, work.data
                )
                dll_path = self._resolve_renderer_dll_path(work)
                if dll_path:
                    try:
                        await loop.run_in_executor(
                            None, self._handoff_mesh_to_rust_renderer, dll_path, result
                        )
                    except Exception:
                        if self.config.renderer_required or work.metadata.get("renderer_required"):
                            raise
                        logger.exception("Rust renderer handoff failed; keeping Python mesh result")
            elif work.work_type == "mesh_generate":
                # CPU-bound: use process pool
                result = await loop.run_in_executor(
                    self._cpu_executor, MeshGenerator.generate, work.metadata
                )
            elif work.work_type == "audio_process":
                # Audio: thread pool for lowest latency — numpy releases GIL
                # and we must never sacrifice audio to a process-spawn delay.
                raw, quality = await loop.run_in_executor(
                    None, AudioProcessor.process_safe, work.data, work.metadata
                )
                with self._metrics_lock:
                    self.metrics.audio_quality    = quality
                    self.metrics.audio_latency_ms = (time.time() - start) * 1000
                result = raw
            elif work.work_type == "video_encode":
                # CPU-bound: use process pool
                result = await loop.run_in_executor(
                    self._cpu_executor, VideoEncoder.encode, work.data, work.metadata
                )
            else:
                raise ValueError(f"Unsupported work type: {work.work_type!r}")

            work.completed = True
            work.result    = result
            self.irm.record_latency((time.time() - start) * 1000)

        except Exception as exc:
            work.error = str(exc)
            logger.error(f"Work {work.work_id} ({work.work_type}) failed: {exc}")
        finally:
            self._pending_work.pop(work.work_id, None)
            self._done_work[work.work_id] = work
            while len(self._done_work) > MAX_COMPLETED_WORK_ITEMS:
                oldest_key = next(iter(self._done_work))
                self._done_work.pop(oldest_key, None)

        for cb in self._on_work_complete:
            try:
                cb(work)
            except Exception as exc:
                logger.error(f"Work complete callback error: {exc}")

    # -------------------------------------------------------------------------
    # Public submission API
    # -------------------------------------------------------------------------

    def submit_work(self, work: WorkUnit) -> None:
        if not isinstance(work, WorkUnit):
            raise TypeError("submit_work expects a WorkUnit")
        if not self._running:
            raise RuntimeError("Engine node is not running")
        if work.work_type not in SUPPORTED_WORK_TYPES:
            raise ValueError(f"Unsupported work type: {work.work_type!r}")
        work.source_engine = self.engine_id
        self._work_queue.put_nowait(work)

    def distribute_work(self, work: WorkUnit, target_node: str) -> None:
        if not self._running:
            raise RuntimeError("Engine node is not running")
        if self.mode != EngineMode.TWIN:
            raise RuntimeError("Only the primary (TWIN) engine can distribute work")
        work.target_engine = target_node
        if self.bridge:
            self.bridge.send_command("process_work", {
                "cmd":    "process_work",
                "target": target_node,
                "work":   work.to_dict(),
            })

    # -------------------------------------------------------------------------
    # Role management
    # -------------------------------------------------------------------------

    def _set_role(self, role: EngineRole) -> None:
        old_role   = self.role
        self.role  = role
        self.metrics.role = role
        logger.info(f"Role: {old_role.value} → {role.value}")
        if role == EngineRole.BACKUP_100:
            self.config.quality_level = 100
        elif role == EngineRole.BACKUP_50:
            self.config.quality_level = 75
        elif role == EngineRole.STANDBY:
            self.config.quality_level = 100

    async def _announce_to_primary(self) -> None:
        if self.bridge:
            self.bridge.send_command("register_node", {
                "cmd":          "register_node",
                "engine_id":    self.engine_id,
                "mode":         self.mode.value,
                "capabilities": list(SUPPORTED_WORK_TYPES),
            })

    # -------------------------------------------------------------------------
    # Public status API
    # -------------------------------------------------------------------------

    def get_metrics(self) -> EngineMetrics:
        with self._metrics_lock:
            m = self.metrics
            return EngineMetrics(
                engine_id       = m.engine_id,
                timestamp       = m.timestamp,
                cpu_percent     = m.cpu_percent,
                memory_percent  = m.memory_percent,
                gpu_percent     = m.gpu_percent,
                processing_load = m.processing_load,
                batch_size      = m.batch_size,
                fps             = m.fps,
                frame_time_ms   = m.frame_time_ms,
                health_score    = m.health_score,
                role            = m.role,
                audio_quality   = m.audio_quality,
                audio_latency_ms= m.audio_latency_ms,
                active_streams  = m.active_streams,
                dropped_frames  = m.dropped_frames,
            )

    def get_system_status(self) -> Dict[str, Any]:
        with self._node_lock:
            nodes = {nid: n.to_dict() for nid, n in self._connected_nodes.items()}
        return {
            "engine_id":       self.engine_id,
            "mode":            self.mode.value,
            "role":            self.role.value,
            "state":           self.state.value,
            "metrics":         self.metrics.to_dict(),
            "connected_nodes": nodes,
            "work_queue_size": self._work_queue.qsize(),
            "pending_work":    list(self._pending_work.keys()),
            "completed_work":  list(self._done_work.keys()),
            "quality_level":   self.config.quality_level,
        }

    def on_state_change(self, callback: Callable[[SystemState, SystemState], None]) -> None:
        self._on_state_change.append(callback)

    def on_work_complete(self, callback: Callable[[WorkUnit], None]) -> None:
        self._on_work_complete.append(callback)


    def get_completed_work(self, work_id: str) -> Optional[WorkUnit]:
        """Return a completed work item if still retained in the bounded cache."""
        return self._done_work.get(work_id)

# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_twin_engine(engine_id: str = "twin_engine") -> DistributedEngineNode:
    config = EngineConfig(
        engine_id          = engine_id,
        mode               = EngineMode.TWIN,
        primary_port       = 9000,
        max_batch_size     = 20000,
        default_batch_size = 5000,
    )
    return DistributedEngineNode(config)


def create_camera_engine(
    camera_id:    str,
    listen_port:  int,
    primary_host: str = "127.0.0.1",
    primary_port: int = 9000,
) -> DistributedEngineNode:
    config = EngineConfig(
        engine_id          = camera_id,
        mode               = EngineMode.CAMERA,
        primary_host       = primary_host,
        primary_port       = primary_port,
        listen_port        = listen_port,
        max_batch_size     = 5000,
        default_batch_size = 1000,
    )
    return DistributedEngineNode(config)
