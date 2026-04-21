"""
Minimal stdlib replacement for `systemd.daemon.notify()`.

The only reason this module exists is to avoid the `systemd-python` pip
package, which requires `libsystemd-dev` and a C compiler to build and
has been finicky on Python 3.13. All we actually use `daemon.notify()`
for is sending `READY=1` and `WATCHDOG=1` strings to systemd's notify
socket -- which is a trivial UNIX datagram write.

Protocol reference:
    https://www.freedesktop.org/software/systemd/man/sd_notify.html

Usage:
    from telem_deps.util.sd_notify import notify
    notify("READY=1")
    notify("WATCHDOG=1")

If the service isn't running under systemd with `Type=notify` (e.g.
during local debugging via `python run_telem.py`), $NOTIFY_SOCKET is
unset and notify() becomes a silent no-op. This matches the behavior
of `daemon.notify()` from systemd-python.
"""

import os
import socket


def notify(state: str) -> bool:
    """
    Send a notify message to systemd.

    Returns True if the message was sent, False if $NOTIFY_SOCKET isn't
    set (i.e. we're not running under systemd) or the send failed. We
    never raise; this function is called from hot loops and must not
    bring down the FSW if the socket glitches.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False

    # systemd supports an "abstract" socket path when the string starts
    # with '@' -- the '@' is replaced by a NUL byte on the wire. Most
    # modern systemd uses a regular filesystem path, but handle both.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode("utf-8"), addr)
        return True
    except OSError:
        return False
