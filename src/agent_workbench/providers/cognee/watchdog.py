"""Bound the isolated process group even when its owning host process dies."""

import math
import os
import select
import signal
import sys


def guard(fd: int, seconds: float) -> None:
    if not math.isfinite(seconds) or not 0 < seconds <= 180:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    # Host never writes. Readability means EOF (parent died/closed) or invalid protocol.
    select.select([fd], [], [], seconds)
    os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    guard(int(sys.argv[1]), float(sys.argv[2]))
