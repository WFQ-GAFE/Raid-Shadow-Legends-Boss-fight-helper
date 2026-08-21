from __future__ import annotations

import os

from controller_pause import ControllerPauseEvent, signal_controller_pause


def main() -> int:
    pid = os.getpid()
    with ControllerPauseEvent(pid) as pause_event:
        assert not pause_event.is_set()
        assert signal_controller_pause(pid)
        assert pause_event.is_set()
    print("controller-pause-tests-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
