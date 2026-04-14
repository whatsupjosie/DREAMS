"""
modules/bridge.py -- adapted twin-engine bridge for PubCast AI v5.5

Built as a compatibility bridge for the EVO distributed engine seam.
Preserves the donor bridge's recovery-first mindset while exposing the
actual API the v5.5 engine expects today.

Notes:
- On Windows, shared-memory support is intentionally disabled rather than faked.
- UDP is the primary node-to-node transport for the EVO layer.
- File spool fallback is real and recoverable when network sends fail.
- Strong references are retained for free-function callbacks so they don't vanish.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("pubcast.bridge")


class BridgeStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    EMERGENCY = "emergency"
    ACK = "ack"
    ERROR = "error"


class MessageType(str, Enum):
    HEARTBEAT = "heartbeat"
    COMMAND = "command"
    METRICS = "metrics"
    STATUS = "status"
    SYSTEM = "system"
    ACK = "ack"
    ERROR = "error"


@dataclass
class BridgeMessage:
    msg_type: Union[MessageType, str]
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source_id: str = ""

    def to_wire_dict(self) -> Dict[str, Any]:
        msg_type = self.msg_type.value if isinstance(self.msg_type, MessageType) else str(self.msg_type)
        return {
            "type": msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "source_id": self.source_id,
        }

    @classmethod
    def from_wire_dict(cls, data: Dict[str, Any]) -> "BridgeMessage":
        raw_type = data.get("type", MessageType.SYSTEM.value)
        try:
            msg_type: Union[MessageType, str] = MessageType(raw_type)
        except Exception:
            msg_type = str(raw_type)
        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            msg_type=msg_type,
            payload=payload,
            timestamp=float(data.get("timestamp", time.time())),
            source_id=str(data.get("source_id", "")),
        )


@dataclass
class BridgeMetrics:
    packets_sent: int = 0
    packets_received: int = 0
    send_failures: int = 0
    receive_failures: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    queue_depth: int = 0
    last_heartbeat_sent: float = 0.0
    last_packet_received: float = 0.0
    spool_writes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "send_failures": self.send_failures,
            "receive_failures": self.receive_failures,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "queue_depth": self.queue_depth,
            "last_heartbeat_sent": self.last_heartbeat_sent,
            "last_packet_received": self.last_packet_received,
            "spool_writes": self.spool_writes,
        }


class UDPBridge:
    """Lightweight UDP transport used by the EVO distributed engine."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        local_port: int = 9001,
        remote_port: int = 9000,
        *,
        timeout_s: float = 0.25,
    ) -> None:
        self.host = host
        self.local_port = local_port
        self.remote_port = remote_port
        self.timeout_s = timeout_s
        self._sock: Optional[socket.socket] = None

    def start(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.local_port))
        sock.settimeout(self.timeout_s)
        self._sock = sock

    def send(self, payload: bytes, *, host: Optional[str] = None, port: Optional[int] = None) -> int:
        if self._sock is None:
            raise RuntimeError("UDPBridge not started")
        return self._sock.sendto(payload, (host or self.host, port or self.remote_port))

    def recv(self, max_bytes: int = 65535) -> Optional[tuple[bytes, tuple[str, int]]]:
        if self._sock is None:
            raise RuntimeError("UDPBridge not started")
        try:
            return self._sock.recvfrom(max_bytes)
        except socket.timeout:
            return None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


class TwinEngineBridge:
    """
    Adapted bridge for the v5.5 EVO seam.

    The bridge is honest about transport:
    - UDP is the active engine-to-engine channel.
    - Windows shared-memory is not claimed.
    - When UDP send fails, messages are recoverably written to disk.
    """

    def __init__(
        self,
        use_shared_memory: bool = False,
        use_udp: bool = True,
        udp_local_port: int = 9001,
        udp_remote_port: int = 9000,
        *,
        host: str = "127.0.0.1",
        data_dir: Optional[Path] = None,
        spool_name: Optional[str] = None,
    ) -> None:
        self.use_shared_memory = use_shared_memory and os.name == "posix"
        self.use_udp = use_udp
        self.host = host
        self.udp_local_port = udp_local_port
        self.udp_remote_port = udp_remote_port
        self.data_dir = Path(data_dir) if data_dir else Path.cwd() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        spool_base = spool_name or f"bridge_{udp_local_port}_{udp_remote_port}"
        self.spool_dir = self.data_dir / "bridge_spool" / spool_base
        self.outbox_dir = self.spool_dir / "outbox"
        self.inbox_dir = self.spool_dir / "inbox"
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        self._status = BridgeStatus.DISCONNECTED
        self._udp = UDPBridge(host=host, local_port=udp_local_port, remote_port=udp_remote_port)
        self._running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[weakref.ReferenceType[Any]]] = {}
        self._strong_callbacks: Dict[str, List[Callable[[BridgeMessage], None]]] = {}
        self._callback_lock = threading.RLock()
        self._metrics = BridgeMetrics()
        self._local_state: Dict[str, Any] = {
            "engine_id": f"engine_{udp_local_port}",
            "mode": "unknown",
            "role": "unknown",
        }
        self._last_remote: Optional[tuple[str, int]] = None
        self._command_sequence = 0
        self._pending_acks: Dict[int, threading.Event] = {}
        self._pending_acks_lock = threading.Lock()
        self._recent_commands: set[tuple[str, int]] = set()
        self._recent_commands_lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return
        try:
            if self.use_udp:
                self._udp.start()
            self._running = True
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                name=f"TwinEngineBridge-{self.udp_local_port}",
                daemon=True,
            )
            self._listener_thread.start()
            self._status = BridgeStatus.CONNECTED if self.use_udp else BridgeStatus.EMERGENCY
            if self.use_shared_memory and os.name != "posix":
                logger.warning("Shared memory requested but unavailable on this platform; using UDP/file fallback")
            logger.info("TwinEngineBridge started on UDP %s -> %s", self.udp_local_port, self.udp_remote_port)
        except OSError as exc:
            self._status = BridgeStatus.EMERGENCY
            logger.warning("UDP start failed, bridge falling back to file spool: %s", exc)
            self._running = True
            self._listener_thread = threading.Thread(
                target=self._spool_watch_loop,
                name=f"TwinEngineBridgeSpool-{self.udp_local_port}",
                daemon=True,
            )
            self._listener_thread.start()

    def stop(self) -> None:
        self._running = False
        self._udp.close()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=1.0)
        self._listener_thread = None
        self._status = BridgeStatus.DISCONNECTED

    close = stop

    def status(self) -> BridgeStatus:
        return self._status

    def on_message(self, message_type: Union[MessageType, str], callback: Callable[[BridgeMessage], None]) -> None:
        key = message_type.value if isinstance(message_type, MessageType) else str(message_type)
        with self._callback_lock:
            refs = self._callbacks.setdefault(key, [])
            try:
                refs.append(weakref.WeakMethod(callback))
            except TypeError:
                refs.append(weakref.ref(callback))
                self._strong_callbacks.setdefault(key, []).append(callback)

    def update_local_state(self, **fields: Any) -> None:
        self._local_state.update(fields)

    def send_heartbeat(self, payload: Optional[Dict[str, Any]] = None, *, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        body = dict(self._local_state)
        if payload:
            body.update(payload)
        body.setdefault("engine_id", self._local_state.get("engine_id", f"engine_{self.udp_local_port}"))
        body.setdefault("bridge_status", self._status.value)
        body["timestamp"] = time.time()
        msg = BridgeMessage(MessageType.HEARTBEAT, body, source_id=str(body.get("engine_id", "")))
        sent = self._send_message(msg, host=host, port=port)
        if sent:
            self._metrics.last_heartbeat_sent = time.time()
        return sent

    def send_metrics(self, payload: Dict[str, Any], *, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        body = dict(self._local_state)
        body.update(payload)
        body["timestamp"] = time.time()
        msg = BridgeMessage(MessageType.METRICS, body, source_id=str(body.get("engine_id", "")))
        return self._send_message(msg, host=host, port=port)

    def send_command(self, command_type: str, payload: Dict[str, Any], priority: int = 0, *, host: Optional[str] = None, port: Optional[int] = None, require_ack: bool = False, ack_timeout_s: float = 0.5) -> bool:
        self._command_sequence += 1
        if command_type == "register_node":
            engine_id = payload.get("engine_id")
            if engine_id:
                self._local_state["engine_id"] = engine_id
            if "mode" in payload:
                self._local_state["mode"] = payload["mode"]
        if command_type == "set_role" and payload.get("role"):
            self._local_state["role"] = payload["role"]
        body = {
            "cmd": command_type,
            "priority": priority,
            "sequence": self._command_sequence,
            **payload,
        }
        msg = BridgeMessage(MessageType.COMMAND, body, source_id=str(self._local_state.get("engine_id", "")))
        ack_event: Optional[threading.Event] = None
        if require_ack:
            ack_event = threading.Event()
            with self._pending_acks_lock:
                self._pending_acks[self._command_sequence] = ack_event
        sent = self._send_message(msg, host=host, port=port)
        if not sent:
            with self._pending_acks_lock:
                self._pending_acks.pop(self._command_sequence, None)
            return False
        if require_ack and ack_event is not None:
            ok = ack_event.wait(timeout=ack_timeout_s)
            with self._pending_acks_lock:
                self._pending_acks.pop(self._command_sequence, None)
            return ok
        return True

    def register_voxel_camera(self, camera_id: str, camera_name: str) -> bool:
        return self.send_command(
            "register_camera",
            {
                "camera_id": camera_id,
                "name": camera_name,
                "type": "voxel_engine",
                "source": f"udp://{self.host}:{self.udp_remote_port}",
            },
        )

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "status": self._status.value,
            "host": self.host,
            "udp_local_port": self.udp_local_port,
            "udp_remote_port": self.udp_remote_port,
            "local_state": dict(self._local_state),
            **self._metrics.to_dict(),
        }

    def _send_message(self, message: BridgeMessage, *, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        wire = json.dumps(message.to_wire_dict(), separators=(",", ":")).encode("utf-8")
        self._metrics.queue_depth = 0
        try:
            if self.use_udp and self._status != BridgeStatus.EMERGENCY:
                sent = self._udp.send(wire, host=host, port=port)
                self._metrics.packets_sent += 1
                self._metrics.bytes_sent += sent
                return True
        except OSError as exc:
            logger.warning("UDP send failed; spooling message instead: %s", exc)
            self._metrics.send_failures += 1
            self._status = BridgeStatus.EMERGENCY
        return self._spool_message(message)

    def _spool_message(self, message: BridgeMessage) -> bool:
        stamp = f"{time.time():.6f}".replace(".", "_")
        path = self.outbox_dir / f"{stamp}_{message.to_wire_dict()['type']}.json"
        try:
            path.write_text(json.dumps(message.to_wire_dict(), indent=2), encoding="utf-8")
            self._metrics.spool_writes += 1
            return True
        except OSError as exc:
            logger.error("Failed to spool bridge message: %s", exc)
            return False

    def _listen_loop(self) -> None:
        while self._running:
            try:
                received = self._udp.recv()
                if not received:
                    continue
                payload, addr = received
                self._last_remote = addr
                self._metrics.packets_received += 1
                self._metrics.bytes_received += len(payload)
                self._metrics.last_packet_received = time.time()
                self._handle_wire_payload(payload, addr)
            except OSError as exc:
                if self._running:
                    self._metrics.receive_failures += 1
                    logger.warning("Bridge receive failed: %s", exc)
            except Exception as exc:
                self._metrics.receive_failures += 1
                logger.error("Bridge listener error: %s", exc)

    def _spool_watch_loop(self) -> None:
        while self._running:
            try:
                for path in sorted(self.inbox_dir.glob("*.json")):
                    try:
                        raw = path.read_bytes()
                        self._handle_wire_payload(raw, None)
                    finally:
                        try:
                            path.unlink()
                        except OSError:
                            pass
                time.sleep(0.25)
            except Exception as exc:
                logger.error("Bridge spool watcher error: %s", exc)
                time.sleep(0.5)

    def _handle_wire_payload(self, payload: bytes, addr: Optional[tuple[str, int]]) -> None:
        data = json.loads(payload.decode("utf-8"))
        msg = BridgeMessage.from_wire_dict(data)
        msg_type = msg.msg_type.value if isinstance(msg.msg_type, MessageType) else str(msg.msg_type)
        if msg_type == MessageType.ACK.value:
            ack_seq = msg.payload.get("ack_sequence")
            if isinstance(ack_seq, int):
                with self._pending_acks_lock:
                    event = self._pending_acks.get(ack_seq)
                if event is not None:
                    event.set()
        elif msg_type == MessageType.COMMAND.value:
            seq = msg.payload.get("sequence")
            source_id = msg.source_id or "unknown"
            if isinstance(seq, int):
                key = (source_id, seq)
                with self._recent_commands_lock:
                    already_seen = key in self._recent_commands
                    if not already_seen:
                        self._recent_commands.add(key)
                if addr is not None:
                    ack = BridgeMessage(MessageType.ACK, {"ack_sequence": seq, "ack_cmd": msg.payload.get("cmd")}, source_id=str(self._local_state.get("engine_id", "")))
                    self._send_message(ack, host=addr[0], port=addr[1])
                if already_seen:
                    return
        self._dispatch(msg_type, msg)

    def _dispatch(self, msg_type: str, message: BridgeMessage) -> None:
        dead: List[weakref.ReferenceType[Any]] = []
        with self._callback_lock:
            callbacks = list(self._callbacks.get(msg_type, []))
        for ref in callbacks:
            callback = ref()
            if callback is None:
                dead.append(ref)
                continue
            try:
                callback(message)
            except Exception as exc:
                logger.error("Bridge callback error for %s: %s", msg_type, exc)
        if dead:
            with self._callback_lock:
                current = self._callbacks.get(msg_type, [])
                self._callbacks[msg_type] = [ref for ref in current if ref not in dead]


VoxelBridge = TwinEngineBridge
