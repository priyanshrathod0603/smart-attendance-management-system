"""Fingerprint adapter layer with a fail-closed Team Office Z305 production stub.

Design principles
-----------------
1. Biometric data isolation: Biometric templates and raw fingerprint images
   remain strictly on the physical BioMax Indigo hardware. AttendanceOS only
   communicates identity identifiers (Teacher ID / device user ID) and verification
   outcomes.
2. Adapter abstraction: High-level AttendanceOS services interact only with
   the abstract ``FingerprintDeviceAdapter`` interface.
3. Resilience: Hardware disconnections, network timeouts, and protocol errors
   fail safely without interrupting Face Recognition or crashing the application.
4. Testability: ``MockFingerprintAdapter`` allows full end-to-end testing of
   attendance workflows in automated suites and offline development.
"""
from __future__ import annotations

import abc
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("biometrics.fingerprint_adapter")


class FingerprintDeviceAdapter(abc.ABC):
    """Abstract base adapter for fingerprint attendance hardware."""

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish connection with the device."""
        raise NotImplementedError

    @abc.abstractmethod
    def disconnect(self) -> bool:
        """Close connection with the device."""
        raise NotImplementedError

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return True if currently connected and healthy."""
        raise NotImplementedError

    @abc.abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Perform a real communication test with the device and return diagnostic metrics."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_device_info(self) -> dict[str, Any]:
        """Return device metadata (model, firmware, connection status, etc.)."""
        raise NotImplementedError

    @abc.abstractmethod
    def enroll_user(self, user_id: str, name: str) -> dict[str, Any]:
        """Initiate device-side fingerprint enrollment for the given user ID.

        Biometric capture is performed entirely on the device.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Remove user registration / templates from the device."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_enrollment_status(self, user_id: str) -> dict[str, Any]:
        """Check if the user has enrolled fingerprints on the device."""
        raise NotImplementedError

    @abc.abstractmethod
    def parse_event(self, raw_data: Any) -> Optional[dict[str, Any]]:
        """Parse incoming push/webhook/socket event payload into standardized event dict."""
        raise NotImplementedError

    def fetch_attendance_logs(self, cursor: Optional[str] = None) -> dict[str, Any]:
        """Return logs newer than cursor, or an actionable unsupported status."""
        return {
            "ok": False,
            "status": "UNSUPPORTED",
            "logs": [],
            "cursor": cursor,
            "simulated": False,
            "error": (
                "Attendance log retrieval is not implemented. Install the official SDK and "
                "Z305-variant/firmware-specific documentation before enabling polling."
            ),
        }


class TeamOfficeZ305Adapter(FingerprintDeviceAdapter):
    """Network reachability adapter; Team Office Z305 protocol is not yet verified."""

    DEFAULT_PORT = None
    DEFAULT_TIMEOUT = 5.0  # seconds

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        device_id: Optional[str] = None,
        comm_key: int | str = 0,
        protocol: str = "TCP_IP",
        timeout: Optional[float] = None,
    ) -> None:
        self.host = host or os.getenv("BIOMAX_DEVICE_HOST") or None
        configured_port = port if port is not None else os.getenv("BIOMAX_DEVICE_PORT")
        self.port = int(configured_port) if configured_port not in (None, "") else None
        self.device_id = device_id or os.getenv("BIOMAX_DEVICE_ID", "TEAM_OFFICE_Z305_01")
        try:
            self.comm_key = int(comm_key or os.getenv("BIOMAX_COMM_KEY", "0"))
        except (ValueError, TypeError):
            self.comm_key = 0
        self.protocol = (protocol or "TCP_IP").upper()
        self.timeout = float(timeout or os.getenv("BIOMAX_TIMEOUT", str(self.DEFAULT_TIMEOUT)))
        self._connected: bool = False
        self._network_reachable: bool = False
        self._socket_connected: bool = False
        self._sdk_ready: bool = False
        self._device_authenticated: bool = False
        self._protocol_verified: bool = False
        self._event_sync_active: bool = False
        self._last_checked: float = 0.0
        self._last_connected_at: Optional[datetime] = None
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None

    def _unsupported_hardware_error(self, action: str, user_id: str | None = None) -> dict[str, Any]:
        """Return a safe, explicit error when the real BioMax SDK/protocol is unavailable."""
        uid = str(user_id).strip() if user_id is not None else None
        msg = (
            "This build does not include a Team Office SDK or a Z305-specific verified protocol implementation. "
            f"{action} cannot be confirmed without the exact device variant, firmware and vendor contract."
        )
        return {
            "ok": False,
            "status": "UNVERIFIED",
            "device_id": self.device_id,
            "user_id": uid,
            "connected": bool(self._connected),
            "network_reachable": bool(self._network_reachable),
            "protocol_verified": False,
            "message": msg,
            "error": msg,
        }

    def reconfigure(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        device_id: Optional[str] = None,
        comm_key: Optional[int | str] = None,
        protocol: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        with self._lock:
            if host is not None:
                self.host = str(host).strip()
            if port is not None:
                self.port = int(port)
            if device_id is not None:
                self.device_id = str(device_id).strip()
            if comm_key is not None:
                try:
                    self.comm_key = int(comm_key)
                except (ValueError, TypeError):
                    self.comm_key = 0
            if protocol is not None:
                self.protocol = str(protocol).strip().upper()
            if timeout is not None:
                self.timeout = float(timeout)
            self._connected = False
            self._network_reachable = False
            self._socket_connected = False
            self._sdk_ready = False
            self._device_authenticated = False
            self._protocol_verified = False
            self._event_sync_active = False
            self._last_error = None
            self._last_checked = 0.0

    def connect(self) -> bool:
        """Verify socket reachability and distinguish it from protocol-level operational readiness."""
        with self._lock:
            if not self.host or not self.port:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = (
                    "Configure the terminal IP address and the TCP port confirmed by Team Office "
                    "for this exact Z305 variant and firmware. No port is assumed by AttendanceOS."
                )
                self._last_checked = time.time()
                return False
            logger.info(f"[BIOMAX_NET] Connection attempt to BioMax Indigo terminal at {self.host}:{self.port} (timeout={self.timeout}s)...")
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                result = sock.connect_ex((self.host, self.port))
                sock.close()

                if result == 0:
                    self._connected = True
                    self._network_reachable = True
                    self._socket_connected = True
                    self._sdk_ready = False
                    self._device_authenticated = False
                    self._protocol_verified = False
                    self._event_sync_active = False
                    self._last_error = None
                    self._last_checked = time.time()
                    self._last_connected_at = datetime.now(timezone.utc)
                    logger.info(
                        "[BIOMAX_NET] TCP socket to %s:%s is reachable, but no BioMax protocol-level "
                        "handshake has been verified in this environment.",
                        self.host,
                        self.port,
                    )
                    return True
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = f"Socket connection returned error code {result} (Host unreachable / Port closed)"
                self._last_checked = time.time()
                logger.warning(f"[BIOMAX_NET] Connection to {self.host}:{self.port} FAILED: {self._last_error}")
                return False
            except socket.timeout:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = f"Connection timeout after {self.timeout}s to {self.host}:{self.port}"
                self._last_checked = time.time()
                logger.warning(f"[BIOMAX_NET] Connection to {self.host}:{self.port} TIMED OUT after {self.timeout}s")
                return False
            except Exception as e:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = str(e)
                self._last_checked = time.time()
                logger.error(f"[BIOMAX_NET] Connection to {self.host}:{self.port} ERROR: {str(e)}")
                return False

    def disconnect(self) -> bool:
        with self._lock:
            self._connected = False
            self._network_reachable = False
            self._socket_connected = False
            self._sdk_ready = False
            self._device_authenticated = False
            self._protocol_verified = False
            self._event_sync_active = False
            logger.info(f"[BIOMAX_NET] Disconnected from {self.host}:{self.port}")
            return True

    def is_connected(self) -> bool:
        return self._connected

    def test_connection(self) -> dict[str, Any]:
        """Perform a real socket communication test measuring exact round-trip latency."""
        start = time.perf_counter()
        if not self.host or not self.port:
            error = (
                "Configure the terminal IP address and the TCP port confirmed by Team Office "
                "for this exact Z305 variant and firmware. No port is assumed by AttendanceOS."
            )
            with self._lock:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = error
                self._last_checked = time.time()
            return {
                "ok": False,
                "status": "NOT_CONFIGURED",
                "latency_ms": 0.0,
                "device": "Team Office Z305",
                "host": self.host,
                "port": self.port,
                "protocol": self.protocol,
                "communication": "NOT_CONFIGURED",
                "message": error,
                "device_info": self.get_device_info(),
            }
        logger.info(f"[BIOMAX_NET] Diagnostic test connection to {self.host}:{self.port} initiated...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((self.host, self.port))
            sock.close()
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

            if result == 0:
                with self._lock:
                    self._connected = True
                    self._network_reachable = True
                    self._socket_connected = True
                    self._sdk_ready = False
                    self._device_authenticated = False
                    self._protocol_verified = False
                    self._event_sync_active = False
                    self._last_error = None
                    self._last_checked = time.time()
                    self._last_connected_at = datetime.now(timezone.utc)
                logger.info(f"[BIOMAX_NET] TCP reachability test SUCCEEDED: latency={elapsed_ms}ms at {self.host}:{self.port}")
                return {
                    "ok": True,
                    "status": "NETWORK_REACHABLE",
                    "latency_ms": elapsed_ms,
                    "device": "Team Office Z305",
                    "host": self.host,
                    "port": self.port,
                    "protocol": self.protocol,
                    "communication": "TCP_REACHABLE",
                    "message": f"TCP socket reachable at {self.host}:{self.port} ({elapsed_ms} ms); Team Office Z305 protocol and device operation are not verified.",
                    "device_info": self.get_device_info(),
                }
            else:
                err_msg = f"Socket error code {result}: Device not reachable at {self.host}:{self.port}."
                with self._lock:
                    self._connected = False
                    self._network_reachable = False
                    self._socket_connected = False
                    self._sdk_ready = False
                    self._device_authenticated = False
                    self._protocol_verified = False
                    self._event_sync_active = False
                    self._last_error = err_msg
                    self._last_checked = time.time()
                logger.warning(f"[BIOMAX_NET] Diagnostic test connection FAILED: latency={elapsed_ms}ms, error={err_msg}")
                return {
                    "ok": False,
                    "status": "FAILED",
                    "latency_ms": elapsed_ms,
                    "device": "Team Office Z305",
                    "host": self.host,
                    "port": self.port,
                    "protocol": self.protocol,
                    "communication": "FAILED",
                    "message": f"Unable to connect to device. Reason: {err_msg}",
                    "device_info": self.get_device_info(),
                }
        except socket.timeout:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            err_msg = f"Connection timed out after {self.timeout}s to {self.host}:{self.port}."
            with self._lock:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = err_msg
                self._last_checked = time.time()
            logger.warning(f"[BIOMAX_NET] Diagnostic test connection TIMED OUT after {self.timeout}s to {self.host}:{self.port}")
            return {
                "ok": False,
                "status": "TIMEOUT",
                "latency_ms": elapsed_ms,
                "device": "Team Office Z305",
                "host": self.host,
                "port": self.port,
                "protocol": self.protocol,
                "communication": "TIMEOUT",
                "message": f"Unable to connect to device. Reason: {err_msg}",
                "device_info": self.get_device_info(),
            }
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            with self._lock:
                self._connected = False
                self._network_reachable = False
                self._socket_connected = False
                self._sdk_ready = False
                self._device_authenticated = False
                self._protocol_verified = False
                self._event_sync_active = False
                self._last_error = str(e)
                self._last_checked = time.time()
            logger.error(f"[BIOMAX_NET] Diagnostic test connection ERROR at {self.host}:{self.port}: {str(e)}")
            return {
                "ok": False,
                "status": "ERROR",
                "latency_ms": elapsed_ms,
                "device": "Team Office Z305",
                "host": self.host,
                "port": self.port,
                "protocol": self.protocol,
                "communication": "ERROR",
                "message": f"Unable to connect to device. Reason: {str(e)}",
                "device_info": self.get_device_info(),
            }

    def get_device_info(self) -> dict[str, Any]:
        status_label = (
            "operational" if self._connected and self._protocol_verified and self._device_authenticated
            else "network_reachable" if self._network_reachable
            else "error" if self._last_error
            else "disconnected"
        )
        return {
            "device_id": self.device_id,
            "name": "Team Office Z305 Terminal",
            "model": "Team Office Z305 (variant/firmware unverified)",
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
            "connected": self._connected,
            "operation_status": "network_reachable" if self._network_reachable else "offline",
            "status": status_label,
            "network_reachable": self._network_reachable,
            "network_connected": self._network_reachable,
            "socket_connected": self._socket_connected,
            "sdk_ready": self._sdk_ready,
            "device_authenticated": self._device_authenticated,
            "protocol_verified": self._protocol_verified,
            "event_sync_active": self._event_sync_active,
            "enrollment_supported": False,
            "attendance_sync_working": self._event_sync_active,
            "integration_ready": bool(self._connected and self._protocol_verified and self._device_authenticated),
            "port_role": "TCP endpoint reachability only; Team Office Z305 service/protocol not verified",
            "attendance_log_pull_supported": False,
            "event_subscription_supported": False,
            "fingerprint_enrollment_supported": False,
            "operational": bool(self._connected and self._protocol_verified and self._device_authenticated),
            "verification_required": True,
            "last_error": self._last_error,
            "serial_number": "Not Available (Hardware Query Required)",
            "firmware_version": "Not Available (Hardware Query Required)",
            "user_count": "Not Available",
            "fingerprint_count": "Not Available",
            "last_connected_at": self._last_connected_at.isoformat() if self._last_connected_at else None,
            "last_communication": datetime.now(timezone.utc).isoformat() if self._connected else None,
            "biometric_storage": "device_local_secure_hardware",
            "supported_methods": ["FINGERPRINT"],
            "is_mock": False,
            "sdk_status": "TCP reachability only; Team Office Z305 SDK/protocol support is unverified",
        }

    def enroll_user(self, user_id: str, name: str) -> dict[str, Any]:
        """Trigger remote enrollment on the BioMax Indigo terminal for user_id.

        Real device enrollment requires the exact BioMax SDK/protocol for the
        actual terminal model. This adapter intentionally fails closed rather than
        reporting a false success when the real hardware contract is unavailable.
        """
        clean_uid = str(user_id).strip()
        if not clean_uid:
            return {"ok": False, "error": "user_id is required", "status": "INVALID_INPUT"}

        if not self.is_connected():
            return {
                "ok": False,
                "error": f"Team Office Z305 device ({self.host}:{self.port}) is currently offline or unreachable. "
                         "Please ensure the device is powered on and connected to the network before enrollment.",
                "connected": False,
                "user_id": clean_uid,
                "status": "OFFLINE",
            }

        return self._unsupported_hardware_error("Enrollment", clean_uid)

    def delete_user(self, user_id: str) -> dict[str, Any]:
        clean_uid = str(user_id).strip()
        if not clean_uid:
            return {"ok": False, "error": "user_id is required", "status": "INVALID_INPUT"}

        if not self.is_connected():
            return {
                "ok": False,
                "error": f"BioMax Indigo device ({self.host}:{self.port}) is currently offline or unreachable.",
                "connected": False,
                "user_id": clean_uid,
                "status": "OFFLINE",
            }

        return self._unsupported_hardware_error("User deletion", clean_uid)

    def get_enrollment_status(self, user_id: str) -> dict[str, Any]:
        clean_uid = str(user_id).strip()
        return {
            "user_id": clean_uid,
            "device_id": self.device_id,
            "device_connected": self.is_connected(),
            "network_reachable": bool(self._network_reachable),
            "enrolled": False,
            "verified": False,
            "status": "UNVERIFIED",
            "biometric_type": "FINGERPRINT",
            "message": "No protocol-level enrollment verification is available; obtain the Team Office SDK and Z305-specific firmware documentation.",
        }

    def parse_event(self, raw_data: Any) -> Optional[dict[str, Any]]:
        """Parse the normalized AttendanceOS event shape, not an assumed vendor protocol."""
        if not isinstance(raw_data, dict):
            logger.warning("[FINGERPRINT_PARSER] Rejected non-object payload: type=%s", type(raw_data))
            return None

        device_id = raw_data.get("device_id")
        user_id = raw_data.get("user_id")
        raw_time = raw_data.get("timestamp")
        verify_mode = raw_data.get("verify_type")
        if (
            not isinstance(device_id, str) or not device_id.strip()
            or not isinstance(user_id, str) or not user_id.strip()
            or not isinstance(raw_time, str)
            or not isinstance(verify_mode, str) or verify_mode.strip().upper() != "FINGERPRINT"
        ):
            return None

        timestamp_text = raw_time.strip()
        if timestamp_text.endswith(("Z", "z")):
            timestamp_text = timestamp_text[:-1] + "+00:00"
        try:
            event_time = datetime.fromisoformat(timestamp_text)
        except ValueError:
            return None
        if event_time.tzinfo is None:
            return None

        parsed = {
            "user_id": user_id.strip(),
            "device_id": str(device_id),
            "event_time": event_time,
            "verify_type": "FINGERPRINT",
            "confidence": 1.0,
            "raw": raw_data,
        }
        logger.info(
            "[FINGERPRINT_PARSER] Normalized event parsed: device_id=%s user_id=%s event_time=%s",
            device_id,
            user_id,
            parsed["event_time"].isoformat(),
        )
        return parsed

    def fetch_attendance_logs(self, cursor: Optional[str] = None) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "UNSUPPORTED",
            "logs": [],
            "cursor": cursor,
            "simulated": False,
            "error": (
                "Team Office Z305 attendance-log polling is unavailable until the vendor supplies "
                "the matching SDK, model/firmware support matrix, protocol docs and sample log-pull application."
            ),
        }


# Backwards-compatible import name; this class does not implement BioMax-specific commands.
BioMaxIndigoAdapter = TeamOfficeZ305Adapter


class MockFingerprintAdapter(FingerprintDeviceAdapter):
    """In-memory mock adapter for automated testing and offline simulation."""

    def __init__(
        self,
        device_id: str = "MOCK_BIOMAX_INDIGO",
        initially_connected: bool = True,
    ) -> None:
        self.device_id = device_id
        self._connected: bool = initially_connected
        self._enrolled_users: Dict[str, Dict[str, Any]] = {}
        self._event_log: List[Dict[str, Any]] = []
        self._last_connected_at: Optional[datetime] = datetime.now(timezone.utc) if initially_connected else None
        self._last_error: Optional[str] = None

    def connect(self) -> bool:
        self._connected = True
        self._last_error = None
        self._last_connected_at = datetime.now(timezone.utc)
        return True

    def disconnect(self) -> bool:
        self._connected = False
        return True

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, status: bool) -> None:
        """Helper for unit tests to simulate network drops."""
        self._connected = status
        if status:
            self._last_error = None
            self._last_connected_at = datetime.now(timezone.utc)
        else:
            self._last_error = "Device simulated offline state"

    def test_connection(self) -> dict[str, Any]:
        """Perform simulated connection test."""
        if self._connected:
            return {
                "ok": True,
                "status": "SUCCESS",
                "latency_ms": 14.5,
                "device": "BioMax Indigo Series (Mock Simulator)",
                "host": "127.0.0.1",
                "port": 4370,
                "protocol": "MOCK",
                "communication": "OK",
                "message": "Mock BioMax Indigo terminal responded with OK (Simulation Mode).",
                "device_info": self.get_device_info(),
            }
        else:
            return {
                "ok": False,
                "status": "FAILED",
                "latency_ms": 5000.0,
                "device": "BioMax Indigo Series (Mock Simulator)",
                "host": "127.0.0.1",
                "port": 4370,
                "protocol": "MOCK",
                "communication": "FAILED",
                "message": "Unable to connect to device. Reason: Mock device is currently simulated in disconnected state.",
                "device_info": self.get_device_info(),
            }

    def get_device_info(self) -> dict[str, Any]:
        status_label = "connected" if self._connected else "disconnected"
        return {
            "device_id": self.device_id,
            "name": "Mock BioMax Indigo Simulator",
            "model": "BioMax Indigo Series (Mock Simulator)",
            "host": "127.0.0.1",
            "port": 4370,
            "protocol": "MOCK",
            "connected": self._connected,
            "status": status_label,
            "last_error": self._last_error,
            "serial_number": "MOCK-INDIGO-998822",
            "firmware_version": "v3.8.4-mock-test",
            "user_count": len(self._enrolled_users),
            "fingerprint_count": len(self._enrolled_users),
            "last_connected_at": self._last_connected_at.isoformat() if self._last_connected_at else None,
            "last_communication": datetime.now(timezone.utc).isoformat() if self._connected else None,
            "biometric_storage": "device_local_secure_mock",
            "supported_methods": ["FINGERPRINT"],
            "is_mock": True,
            "attendance_log_pull_supported": True,
            "event_subscription_supported": True,
            "fingerprint_enrollment_supported": True,
            "sdk_status": "MOCK / DEVELOPMENT ONLY (Simulator Mode Active)",
        }

    def enroll_user(self, user_id: str, name: str) -> dict[str, Any]:
        clean_uid = str(user_id).strip()
        if not self._connected:
            return {
                "ok": False,
                "error": "Device is offline or disconnected.",
                "connected": False,
                "user_id": clean_uid,
            }
        self._enrolled_users[clean_uid] = {
            "user_id": clean_uid,
            "name": name,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "ok": True,
            "user_id": clean_uid,
            "name": name,
            "device_id": self.device_id,
            "status": "simulated",
            "simulated": True,
            "message": f"Simulated enrollment for teacher ID {clean_uid}; no physical device was changed.",
        }

    def delete_user(self, user_id: str) -> dict[str, Any]:
        clean_uid = str(user_id).strip()
        if clean_uid in self._enrolled_users:
            del self._enrolled_users[clean_uid]
        return {
            "ok": True,
            "user_id": clean_uid,
            "device_id": self.device_id,
            "status": "simulated",
            "simulated": True,
            "message": f"Simulated deletion for teacher ID {clean_uid}; no physical device was changed.",
        }

    def get_enrollment_status(self, user_id: str) -> dict[str, Any]:
        clean_uid = str(user_id).strip()
        enrolled = clean_uid in self._enrolled_users
        return {
            "user_id": clean_uid,
            "device_id": self.device_id,
            "device_connected": self._connected,
            "enrolled": enrolled,
            "verified": enrolled,
            "simulated": True,
            "status": "SIMULATED_ENROLLED" if enrolled else "NOT_ENROLLED",
            "biometric_type": "FINGERPRINT",
        }

    def parse_event(self, raw_data: Any) -> Optional[dict[str, Any]]:
        if not raw_data or not isinstance(raw_data, dict):
            return None
        user_id = raw_data.get("user_id") or raw_data.get("teacher_id")
        if not user_id:
            return None
        raw_time = raw_data.get("timestamp") or raw_data.get("time") or raw_data.get("LogTime")
        event_time: Optional[datetime] = None
        if raw_time:
            if isinstance(raw_time, datetime):
                event_time = raw_time
            elif isinstance(raw_time, str):
                try:
                    event_time = datetime.fromisoformat(raw_time)
                except ValueError:
                    try:
                        event_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        event_time = None
        if event_time is None:
            event_time = datetime.now(timezone.utc)

        return {
            "user_id": str(user_id).strip(),
            "device_id": str(raw_data.get("device_id") or self.device_id),
            "event_time": event_time,
            "verify_type": "FINGERPRINT",
            "confidence": float(raw_data.get("confidence", 1.0)),
            "raw": raw_data,
        }

    def queue_event(self, raw_data: dict[str, Any]) -> None:
        """Queue a simulated device log for tests; it never represents physical hardware."""
        self._event_log.append(dict(raw_data))

    def fetch_attendance_logs(self, cursor: Optional[str] = None) -> dict[str, Any]:
        if not self._connected:
            return {
                "ok": False,
                "status": "OFFLINE",
                "logs": [],
                "cursor": cursor,
                "simulated": True,
                "error": "Mock device is offline.",
            }
        try:
            start = max(0, int(cursor or 0))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "status": "INVALID_CURSOR",
                "logs": [],
                "cursor": cursor,
                "simulated": True,
                "error": "Mock log cursor must be an integer offset.",
            }
        logs = self._event_log[start:]
        return {
            "ok": True,
            "status": "SUCCESS",
            "logs": [dict(event) for event in logs],
            "cursor": str(len(self._event_log)),
            "simulated": True,
        }


# ---------------------------------------------------------------------------
# Singleton / Factory management
# ---------------------------------------------------------------------------
_ADAPTER_INSTANCE: Optional[FingerprintDeviceAdapter] = None
_ADAPTER_LOCK = threading.Lock()


def get_fingerprint_adapter(
    adapter_type: Optional[str] = None,
    force_new: bool = False,
) -> FingerprintDeviceAdapter:
    """Return the global fingerprint device adapter instance."""
    global _ADAPTER_INSTANCE

    if _ADAPTER_INSTANCE is not None and not force_new:
        return _ADAPTER_INSTANCE

    with _ADAPTER_LOCK:
        if _ADAPTER_INSTANCE is not None and not force_new:
            return _ADAPTER_INSTANCE

        kind = (
            adapter_type
            or os.getenv("FINGERPRINT_ADAPTER_TYPE")
            or "team_office_z305"
        ).lower().strip()

        if kind == "mock":
            _ADAPTER_INSTANCE = MockFingerprintAdapter()
        elif kind in {"team_office_z305", "biomax"}:
            _ADAPTER_INSTANCE = TeamOfficeZ305Adapter()
        else:
            raise ValueError(f"Unsupported fingerprint adapter type: {kind}")

        return _ADAPTER_INSTANCE


def set_fingerprint_adapter(adapter: FingerprintDeviceAdapter) -> None:
    """Explicitly set the active adapter (useful in test harnesses)."""
    global _ADAPTER_INSTANCE
    with _ADAPTER_LOCK:
        _ADAPTER_INSTANCE = adapter
