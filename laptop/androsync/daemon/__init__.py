"""Daemon package: the background service that the phone app talks to.

``jobs.py`` runs sync jobs on a background thread and keeps their status
and live log around for the HTTP API (``server.py``, added later) to
expose. ``watcher.py`` (added later) drives ``jobs.py`` from ADB device
connect/disconnect events.
"""

from __future__ import annotations
