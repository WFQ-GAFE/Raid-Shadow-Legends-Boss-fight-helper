from __future__ import annotations

import struct
import time

from inject_probe import (
    close_result_pipe,
    create_result_pipe,
    encode_lifecycle_request,
    encode_takeover_request,
    start_result_reader,
)


def expect_value_error(**kwargs) -> None:
    try:
        encode_takeover_request(**kwargs)
    except ValueError:
        return
    raise AssertionError(f"invalid takeover request accepted: {kwargs}")


def main() -> int:
    pipe = create_result_pipe(2_000_000_001)
    result_queue, reader = start_result_reader(pipe)
    del result_queue
    time.sleep(0.05)
    started = time.monotonic()
    close_result_pipe(pipe, reader)
    assert not reader.is_alive()
    assert time.monotonic() - started < 2.5

    user_id = 0x123456789ABCDEF0
    payload = encode_takeover_request(
        session_id=0x0102030405060708,
        active=True,
        expected_user_id=user_id,
    )
    assert len(payload) == 32
    magic, version, session, action, flags, low, high = struct.unpack(
        "<IIQIIII", payload
    )
    assert magic == 0x5243544C
    assert version == 1
    assert session == 0x0102030405060708
    assert action == 1
    assert flags == 1
    assert (high << 32) | low == user_id

    chimera = struct.unpack(
        "<IIQIIII",
        encode_takeover_request(
            session_id=2,
            active=True,
            expected_user_id=3,
            boss_mode="chimera",
        ),
    )
    hydra = struct.unpack(
        "<IIQIIII",
        encode_takeover_request(
            session_id=2,
            active=True,
            expected_user_id=3,
            boss_mode="hydra",
        ),
    )
    assert chimera[4] == 3
    assert hydra[4] == 7

    end = struct.unpack(
        "<IIQIIII",
        encode_takeover_request(session_id=9, active=False),
    )
    assert end[3:] == (2, 0, 0, 0)

    expect_value_error(session_id=0, active=True, expected_user_id=1)
    expect_value_error(session_id=1, active=True, expected_user_id=None)
    expect_value_error(session_id=1, active=True, expected_user_id=True)
    expect_value_error(session_id=1, active=True, expected_user_id=0)
    expect_value_error(
        session_id=1,
        active=True,
        expected_user_id=1,
        boss_mode="unknown",
    )
    expect_value_error(
        session_id=1,
        active=True,
        expected_user_id=0x1_0000000000000000,
    )
    heroes = [39104, 21597, 34700, 21826, 26679]
    lifecycle = encode_lifecycle_request(
        session_id=7,
        context=0x123456789,
        action=5,
        nonce=99,
        hero_ids=heroes,
    )
    assert len(lifecycle) == 64
    unpacked = struct.unpack("<IIQQIIII5iI", lifecycle)
    assert unpacked[:5] == (0x52434C43, 2, 7, 0x123456789, 5)
    assert list(unpacked[8:13]) == heroes
    assert unpacked[13] == 5
    hydra_restart = encode_lifecycle_request(
        session_id=7,
        context=0x987654321,
        action=6,
        nonce=100,
    )
    assert struct.unpack("<IIQQIIII5iI", hydra_restart)[:5] == (
        0x52434C43,
        2,
        7,
        0x987654321,
        6,
    )
    for invalid in (
        {"session_id": 0, "context": 1, "action": 4, "nonce": 1},
        {"session_id": 1, "context": 0, "action": 4, "nonce": 1},
        {"session_id": 1, "context": 1, "action": 7, "nonce": 1},
        {"session_id": 1, "context": 1, "action": 5, "nonce": 1},
        {
            "session_id": 1,
            "context": 1,
            "action": 5,
            "nonce": 1,
            "hero_ids": [1, 1, 2, 3, 4],
        },
    ):
        try:
            encode_lifecycle_request(**invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid lifecycle request accepted: {invalid}")
    print("inject-protocol-tests-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
