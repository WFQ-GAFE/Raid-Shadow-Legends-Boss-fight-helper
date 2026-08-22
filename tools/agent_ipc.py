from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
from typing import Any


SHARED_STATE_MAGIC = 0x52434950
SHARED_STATE_VERSION = 3
AGENT_BUILD_ID = 2026082207
COMMAND_VERSION = 4
AGENT_STATE_INITIALIZING = 1
AGENT_STATE_READY = 2
AGENT_STATE_FAILED = 3
AGENT_STATE_SHUTTING_DOWN = 4

FILE_MAP_READ = 0x0004


def _slot_type(capacity: int, name: str) -> type[ctypes.Structure]:
    return type(
        name,
        (ctypes.Structure,),
        {
            "_fields_": [
                ("sequence", ctypes.c_int64),
                ("length", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32),
                ("data", ctypes.c_char * capacity),
            ]
        },
    )


AccountJsonSlot = _slot_type(4096, "AccountJsonSlot")
DecisionJsonSlot = _slot_type(262144, "DecisionJsonSlot")
AckJsonSlot = _slot_type(4096, "AckJsonSlot")
LifecycleJsonSlot = _slot_type(16384, "LifecycleJsonSlot")
BattleLedgerJsonSlot = _slot_type(65536, "BattleLedgerJsonSlot")
RotationCatalogJsonSlot = _slot_type(262144, "RotationCatalogJsonSlot")
DiagnosticJsonSlot = _slot_type(8192, "DiagnosticJsonSlot")


class AgentSharedState(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("shared_state_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("build_id", ctypes.c_uint64),
        ("instance_id", ctypes.c_uint64),
        ("command_version", ctypes.c_uint32),
        ("ready_state", ctypes.c_int32),
        ("hooks_ready", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
        ("account", AccountJsonSlot),
        ("decision", DecisionJsonSlot),
        ("acknowledgement", AckJsonSlot),
        ("lifecycle", LifecycleJsonSlot),
        ("battle_ledger", BattleLedgerJsonSlot),
        ("rotation_catalog", RotationCatalogJsonSlot),
        ("diagnostic", DiagnosticJsonSlot),
    ]


class AgentSharedHeader(ctypes.Structure):
    _fields_ = AgentSharedState._fields_[:10]


assert AgentSharedState.account.offset == 48
assert ctypes.sizeof(AgentSharedHeader) == 48
assert ctypes.sizeof(AccountJsonSlot) == 4112
assert ctypes.sizeof(DecisionJsonSlot) == 262160
assert ctypes.sizeof(AckJsonSlot) == 4112
assert ctypes.sizeof(LifecycleJsonSlot) == 16400
assert ctypes.sizeof(BattleLedgerJsonSlot) == 65552
assert ctypes.sizeof(RotationCatalogJsonSlot) == 262160
assert ctypes.sizeof(DiagnosticJsonSlot) == 8208
assert ctypes.sizeof(AgentSharedState) == 622752


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.MapViewOfFile.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_size_t,
]
kernel32.MapViewOfFile.restype = wintypes.LPVOID
kernel32.UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
kernel32.UnmapViewOfFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class AgentIpc:
    def __init__(self, pid: int):
        self.pid = int(pid)
        self.name = rf"Local\RaidChimeraAgentState-{self.pid}"
        self._mapping: int | None = None
        self._view: int | None = None
        self._state: AgentSharedState | None = None

    def open(self) -> AgentIpc:
        if self._state is not None:
            return self
        mapping = kernel32.OpenFileMappingW(FILE_MAP_READ, False, self.name)
        if not mapping:
            raise FileNotFoundError(ctypes.get_last_error(), self.name)
        view = kernel32.MapViewOfFile(
            mapping, FILE_MAP_READ, 0, 0, ctypes.sizeof(AgentSharedState)
        )
        if not view:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(mapping)
            raise ctypes.WinError(error)
        self._mapping = int(mapping)
        self._view = int(view)
        self._state = AgentSharedState.from_address(int(view))
        return self

    def close(self) -> None:
        self._state = None
        if self._view:
            kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if self._mapping:
            kernel32.CloseHandle(self._mapping)
            self._mapping = None

    def __enter__(self) -> AgentIpc:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def state(self) -> AgentSharedState:
        if self._state is None:
            self.open()
        assert self._state is not None
        return self._state

    def header(self) -> dict[str, int | bool]:
        state = self.state
        compatible = (
            state.magic == SHARED_STATE_MAGIC
            and state.shared_state_version == SHARED_STATE_VERSION
            and state.struct_size == ctypes.sizeof(AgentSharedState)
            and state.pid == self.pid
            and state.build_id == AGENT_BUILD_ID
            and state.command_version == COMMAND_VERSION
        )
        return {
            "magic": int(state.magic),
            "sharedStateVersion": int(state.shared_state_version),
            "structSize": int(state.struct_size),
            "pid": int(state.pid),
            "buildId": int(state.build_id),
            "instanceId": int(state.instance_id),
            "commandVersion": int(state.command_version),
            "readyState": int(state.ready_state),
            "hooksReady": bool(state.hooks_ready),
            "compatible": compatible,
            "ready": compatible
            and state.ready_state == AGENT_STATE_READY
            and bool(state.hooks_ready),
        }

    @staticmethod
    def _read_slot(slot: ctypes.Structure, attempts: int = 8) -> str | None:
        capacity = ctypes.sizeof(type(slot)) - 16
        address = ctypes.addressof(slot) + 16
        for _ in range(attempts):
            before = int(slot.sequence)
            if before & 1:
                time.sleep(0)
                continue
            length = int(slot.length)
            if length == 0:
                return None
            if length >= capacity:
                raise ValueError(f"Shared JSON slot has invalid length {length}/{capacity}")
            payload = ctypes.string_at(address, length)
            after = int(slot.sequence)
            if before == after and not (after & 1):
                return payload.decode("utf-8")
        return None

    def read_text(self, slot_name: str) -> str | None:
        slot = getattr(self.state, slot_name)
        return self._read_slot(slot)

    def read_json(self, slot_name: str) -> dict[str, Any] | None:
        text = self.read_text(slot_name)
        if text is None:
            return None
        value = json.loads(text)
        return value if isinstance(value, dict) else None

    def account(self) -> dict[str, Any] | None:
        return self.read_json("account")

    def decision(self) -> dict[str, Any] | None:
        return self.read_json("decision")

    def acknowledgement(self) -> dict[str, Any] | None:
        return self.read_json("acknowledgement")

    def lifecycle(self) -> dict[str, Any] | None:
        return self.read_json("lifecycle")

    def battle_ledger(self) -> dict[str, Any] | None:
        return self.read_json("battle_ledger")

    def rotation_catalog(self) -> dict[str, Any] | None:
        return self.read_json("rotation_catalog")

    def diagnostic(self) -> str | None:
        return self.read_text("diagnostic")

    def wait_until_ready(self, timeout_seconds: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.header()
            if last["ready"]:
                return last
            if last["readyState"] in (AGENT_STATE_FAILED, AGENT_STATE_SHUTTING_DOWN):
                break
            time.sleep(0.05)
        raise TimeoutError(f"Agent did not become ready: {last}")


def read_agent_status(pid: int) -> dict[str, Any] | None:
    name = rf"Local\RaidChimeraAgentState-{int(pid)}"
    mapping = kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
    if not mapping:
        return None
    view = None
    try:
        view = kernel32.MapViewOfFile(
            mapping, FILE_MAP_READ, 0, 0, ctypes.sizeof(AgentSharedHeader)
        )
        if not view:
            raise ctypes.WinError(ctypes.get_last_error())
        state = AgentSharedHeader.from_address(int(view))
        compatible = (
            state.magic == SHARED_STATE_MAGIC
            and state.shared_state_version == SHARED_STATE_VERSION
            and state.struct_size == ctypes.sizeof(AgentSharedState)
            and state.pid == int(pid)
            and state.build_id == AGENT_BUILD_ID
            and state.command_version == COMMAND_VERSION
        )
        return {
            "magic": int(state.magic),
            "sharedStateVersion": int(state.shared_state_version),
            "structSize": int(state.struct_size),
            "pid": int(state.pid),
            "buildId": int(state.build_id),
            "instanceId": int(state.instance_id),
            "commandVersion": int(state.command_version),
            "readyState": int(state.ready_state),
            "hooksReady": bool(state.hooks_ready),
            "compatible": compatible,
            "ready": compatible
            and state.ready_state == AGENT_STATE_READY
            and bool(state.hooks_ready),
        }
    finally:
        if view:
            kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(mapping)
