"""WSGI entry point: ``gunicorn harelphotos.wsgi:app``.

The config is found the usual way, so in a systemd unit set
``Environment=HARELPHOTOS_CONFIG=/etc/harelphotos/config.toml`` rather than
relying on gunicorn's working directory.

Loading at import time is deliberate: a bad config should stop the unit from
starting, visibly in ``systemctl status``, rather than turn every request into
a 500 that only the log explains.
"""

from __future__ import annotations

from .web import wsgi_app

app = wsgi_app()
