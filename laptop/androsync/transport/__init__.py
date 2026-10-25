"""Transport package: file-operation backends for one side of a pair.

Re-exports the :class:`~androsync.transport.base.Transport` ABC and the
:class:`~androsync.transport.local.LocalDirTransport` implementation used
for the laptop side (and, in tests, to fake the phone side too).
"""

from __future__ import annotations

from androsync.transport.base import Transport, TransportError, check_rel
from androsync.transport.local import LocalDirTransport

__all__ = ["Transport", "TransportError", "check_rel", "LocalDirTransport"]
