"""
Remnawave node monitor — lightweight daemon that polls nodes and alerts via Telegram.
Config: Drive/state/remnawave_config.json
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, Optional

from ouroboros.utils import utc_now_iso, append_jsonl

log = logging.getLogger(__name__)


def _load_config(drive_root: pathlib.Path) -> Optional[Dict[str, Any]]:
    cfg_path = drive_root / "state" / "remnawave_config.json"
    if not cfg_path.exists():
        return None
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("remnawave_monitor: failed to load config: %s", e)
        return None


def _fetch_nodes(panel_url: str, api_token: str) -> Optional[list]:
    url = panel_url.rstrip("/") + "/api/nodes"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", [])
    except urllib.error.URLError as e:
        log.warning("remnawave_monitor: HTTP error fetching nodes: %s", e)
        return None
    except Exception as e:
        log.warning("remnawave_monitor: unexpected error fetching nodes: %s", e)
        return None


def _monitor_loop(
    drive_root: pathlib.Path,
    send_message_fn: Callable[[str], None],
) -> None:
    cfg = _load_config(drive_root)
    if not cfg or not cfg.get("enabled", True):
        log.info("remnawave_monitor: disabled or no config, exiting.")
        return

    panel_url: str = cfg["panel_url"]
    api_token: str = cfg["api_token"]
    interval: int = int(cfg.get("check_interval_sec", 60))

    # uuid -> was_connected (bool)
    prev_state: Dict[str, bool] = {}
    first_run = True

    log.info("remnawave_monitor: started, interval=%ds, panel=%s", interval, panel_url)

    while True:
        # Reload config each cycle to pick up changes
        cfg = _load_config(drive_root)
        if not cfg or not cfg.get("enabled", True):
            log.info("remnawave_monitor: disabled via config, exiting.")
            return

        nodes = _fetch_nodes(panel_url, api_token)

        if nodes is None:
            # Log the error to supervisor log but don't spam Telegram
            append_jsonl(drive_root / "logs" / "supervisor.jsonl", {
                "ts": utc_now_iso(),
                "type": "remnawave_fetch_error",
                "panel_url": panel_url,
            })
        else:
            for node in nodes:
                uid: str = node.get("uuid", "")
                name: str = node.get("name", uid)
                address: str = node.get("address", "")
                is_connected: bool = bool(node.get("isConnected", False))
                is_disabled: bool = bool(node.get("isDisabled", False))
                status_msg: str = node.get("lastStatusMessage", "") or ""

                if first_run:
                    prev_state[uid] = is_connected
                    continue

                was_connected = prev_state.get(uid, True)

                if was_connected and not is_connected and not is_disabled:
                    # Node went DOWN
                    msg = (
                        f"⚠️ Нода DOWN: {name} ({address})\n"
                        f"Статус: {status_msg or 'нет сообщения'}"
                    )
                    log.warning("remnawave_monitor: %s", msg)
                    try:
                        send_message_fn(msg)
                    except Exception as e:
                        log.warning("remnawave_monitor: failed to send alert: %s", e)

                elif not was_connected and is_connected:
                    # Node recovered
                    msg = f"✅ Нода восстановлена: {name} ({address})"
                    log.info("remnawave_monitor: %s", msg)
                    try:
                        send_message_fn(msg)
                    except Exception as e:
                        log.warning("remnawave_monitor: failed to send recovery: %s", e)

                prev_state[uid] = is_connected

            if first_run:
                first_run = False
                log.info(
                    "remnawave_monitor: initial state saved, monitoring %d nodes",
                    len(nodes),
                )

        time.sleep(interval)


def start_monitor(
    drive_root: pathlib.Path,
    send_message_fn: Callable[[str], None],
) -> threading.Thread:
    """Start the Remnawave node monitor as a daemon thread."""
    t = threading.Thread(
        target=_monitor_loop,
        args=(drive_root, send_message_fn),
        daemon=True,
        name="remnawave-monitor",
    )
    t.start()
    return t
