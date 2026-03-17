"""
Remnawave node monitor — daemon thread that polls nodes every 60s
and sends Telegram alerts on state changes (down/up).

No LLM calls, no budget cost. Pure HTTP polling.
"""

import json
import logging
import os
import pathlib
import threading
import time
import urllib.request
import urllib.error

log = logging.getLogger("remnawave_monitor")

# Config file on Drive
CONFIG_FILENAME = "remnawave_config.json"
POLL_INTERVAL = 60  # seconds


def _load_config(drive_root: str) -> dict | None:
    """Load remnawave config from Drive."""
    cfg_path = pathlib.Path(drive_root) / "state" / CONFIG_FILENAME
    if not cfg_path.exists():
        return None
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("remnawave_monitor: failed to load config: %s", e)
        return None


def _fetch_nodes(api_url: str, token: str) -> list[dict] | None:
    """Fetch nodes list from Remnawave API. Returns list or None on error."""
    url = api_url.rstrip("/") + "/api/nodes"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", [])
    except urllib.error.URLError as e:
        log.warning("remnawave_monitor: API request failed: %s", e)
        return None
    except Exception as e:
        log.warning("remnawave_monitor: unexpected error fetching nodes: %s", e)
        return None


def _node_state(node: dict) -> str:
    """Return human-readable state: 'up', 'down', 'disabled', 'connecting'."""
    if node.get("isDisabled"):
        return "disabled"
    if node.get("isConnecting"):
        return "connecting"
    if node.get("isConnected"):
        return "up"
    return "down"


def _monitor_loop(drive_root: str, send_message_fn, stop_event: threading.Event):
    """Main monitoring loop. Runs in a daemon thread."""
    prev_states: dict[str, str] = {}  # uuid -> state

    log.info("remnawave_monitor: started")

    while not stop_event.is_set():
        cfg = _load_config(drive_root)
        if not cfg or not cfg.get("api_url") or not cfg.get("token"):
            stop_event.wait(POLL_INTERVAL)
            continue

        api_url = cfg["api_url"]
        token = cfg["token"]
        nodes = _fetch_nodes(api_url, token)

        if nodes is None:
            # API unreachable — alert if first time
            if "___api___" not in prev_states:
                prev_states["___api___"] = "up"
            if prev_states["___api___"] != "down":
                prev_states["___api___"] = "down"
                try:
                    send_message_fn(
                        "🔴 *Remnawave панель недоступна*\n"
                        f"API: `{api_url}` не отвечает"
                    )
                except Exception as e:
                    log.warning("remnawave_monitor: send_message failed: %s", e)
            stop_event.wait(POLL_INTERVAL)
            continue
        else:
            if prev_states.get("___api___") == "down":
                prev_states["___api___"] = "up"
                try:
                    send_message_fn(
                        "🟢 *Remnawave панель снова доступна*\n"
                        f"API: `{api_url}`"
                    )
                except Exception as e:
                    log.warning("remnawave_monitor: send_message failed: %s", e)

        for node in nodes:
            uuid = node["uuid"]
            name = node.get("name", uuid)
            address = node.get("address", "?")
            state = _node_state(node)
            last_msg = node.get("lastStatusMessage", "")
            users_online = node.get("usersOnline", 0)

            prev = prev_states.get(uuid)

            if prev is None:
                # First poll — just record state, don't alert
                prev_states[uuid] = state
                continue

            if prev == state:
                continue

            # State changed!
            prev_states[uuid] = state
            log.info(
                "remnawave_monitor: node %s (%s) changed %s -> %s",
                name, address, prev, state,
            )

            if state == "down":
                msg = (
                    f"🔴 *Нода упала*: `{name}`\n"
                    f"Адрес: `{address}`\n"
                    f"Было онлайн: {users_online} пользователей"
                )
                if last_msg:
                    msg += f"\nСтатус: _{last_msg}_"
            elif state == "up" and prev in ("down", "connecting"):
                msg = (
                    f"🟢 *Нода восстановлена*: `{name}`\n"
                    f"Адрес: `{address}`\n"
                    f"Онлайн: {users_online} пользователей"
                )
            elif state == "connecting":
                msg = (
                    f"🟡 *Нода переподключается*: `{name}`\n"
                    f"Адрес: `{address}`"
                )
            elif state == "disabled":
                msg = f"⚫ *Нода отключена*: `{name}` (`{address}`)"
            else:
                msg = (
                    f"⚠️ *Нода изменила статус*: `{name}` ({address})\n"
                    f"`{prev}` → `{state}`"
                )

            try:
                send_message_fn(msg)
            except Exception as e:
                log.warning("remnawave_monitor: send_message failed: %s", e)

        stop_event.wait(POLL_INTERVAL)

    log.info("remnawave_monitor: stopped")


def start_monitor(drive_root: str, send_message_fn) -> threading.Thread:
    """
    Start the node monitoring daemon thread.

    Args:
        drive_root: path to Drive root (MyDrive/Ouroboros/)
        send_message_fn: callable(text: str) that sends a Telegram message

    Returns:
        The daemon thread (already started).
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_monitor_loop,
        args=(drive_root, send_message_fn, stop_event),
        name="remnawave-monitor",
        daemon=True,
    )
    thread.start()
    return thread
