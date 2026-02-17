"""Background channel management — bus mode with ChannelManager.

Architecture:
  Bus thread: runs ChannelManager + all channels + inbound consumer.
  Main CLI thread: runs agent invocations (to avoid event-loop conflicts).

The inbound consumer does NOT call the agent directly.  Instead it
enqueues a ``ChannelMessage`` on a thread-safe ``queue.Queue`` and waits
for the main thread to set a response via ``_set_channel_response()``.
"""

import asyncio
import logging
import queue
import time
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..stream.display import console

_channel_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queue bridge: bus thread  ⇄  main CLI thread
# ---------------------------------------------------------------------------

@dataclass
class ChannelMessage:
    """A message from a channel, enqueued for the main CLI thread."""
    msg_id: str
    content: str
    sender: str
    channel_type: str
    metadata: dict | None = None
    # Filled by the bus consumer so the main thread can send callbacks
    channel_ref: Any = None  # Channel instance (for thinking / todo / file)
    bus_ref: Any = None      # MessageBus (for publishing outbound)
    chat_id: str = ""
    message_id: str | None = None


# Thread-safe queue: bus → main
_message_queue: queue.Queue[ChannelMessage] = queue.Queue()

# Pending responses: main → bus (msg_id → {"event": Event, "response": str|None})
_pending_responses: dict[str, dict] = {}
_response_lock = threading.Lock()


def _enqueue_channel_message(msg: ChannelMessage) -> threading.Event:
    """Enqueue a channel message for the main thread and return a wait event."""
    event = threading.Event()
    with _response_lock:
        _pending_responses[msg.msg_id] = {"event": event, "response": None}
    _message_queue.put(msg)
    return event


def _set_channel_response(msg_id: str, response: str) -> None:
    """Set the response for a channel message and unblock the bus consumer."""
    with _response_lock:
        slot = _pending_responses.get(msg_id)
        if slot:
            slot["response"] = response
            slot["event"].set()


def _pop_channel_response(msg_id: str) -> str | None:
    """Retrieve and remove the response for a channel message."""
    with _response_lock:
        slot = _pending_responses.pop(msg_id, None)
    return slot["response"] if slot else None


# ---------------------------------------------------------------------------
# Module-level channel state (bus mode)
# ---------------------------------------------------------------------------

_manager: Optional[Any] = None  # ChannelManager
_bus_loop: Optional[asyncio.AbstractEventLoop] = None
_bus_thread: Optional[threading.Thread] = None
_cli_agent: Any = None          # shared agent reference (same as CLI)
_cli_thread_id: Optional[str] = None  # shared thread_id (same conversation)


def _channels_is_running(channel_type: str | None = None) -> bool:
    """Check whether channels are running."""
    if _manager is None:
        return False
    if channel_type:
        ch = _manager.get_channel(channel_type)
        return ch is not None and ch._running
    return _manager.is_running and bool(_manager.running_channels())


def _channels_running_list() -> list[str]:
    """Return names of running channels."""
    return _manager.running_channels() if _manager else []


def _channels_stop(channel_type: str | None = None) -> None:
    """Stop channel(s) and clean up module-level state."""
    global _manager, _bus_loop, _bus_thread, _cli_agent, _cli_thread_id

    if channel_type is None:
        # Stop everything
        if _bus_loop and _manager:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    _manager.stop_all(), _bus_loop,
                )
                future.result(timeout=10)
            except Exception as e:
                _channel_logger.debug(f"Error stopping channels: {e}")
        if _manager:
            _manager.bus.stop()
        if _bus_thread:
            _bus_thread.join(timeout=5)
        _manager = None
        _bus_loop = None
        _bus_thread = None
        _cli_agent = None
        _cli_thread_id = None
        return

    # Stop a specific channel
    if _manager and _bus_loop:
        try:
            future = asyncio.run_coroutine_threadsafe(
                _manager.remove_channel(channel_type), _bus_loop,
            )
            future.result(timeout=5)
        except Exception as e:
            _channel_logger.debug(f"Error removing channel {channel_type}: {e}")

    if _manager and not _manager.running_channels():
        _cli_agent = None
        _cli_thread_id = None


def _start_channels_bus_mode(
    config,
    agent,
    thread_id: str,
    *,
    send_thinking: bool | None = None,
) -> None:
    """Start all channels in bus mode with MessageBus + ChannelManager.

    Creates a single event loop in a daemon thread running the bus,
    ChannelManager, and the inbound consumer.
    """
    global _manager, _bus_loop, _bus_thread

    from ..channels.channel_manager import ChannelManager

    mgr = ChannelManager.from_config(config)

    effective_send_thinking = (
        getattr(config, "channel_send_thinking", True)
        if send_thinking is None
        else send_thinking
    )
    for channel in mgr._channels.values():
        channel.send_thinking = bool(effective_send_thinking)

    _manager = mgr

    def _bus_thread_entry():
        global _bus_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _bus_loop = loop

        async def _run():
            consumer = asyncio.create_task(
                _bus_inbound_consumer(mgr.bus, mgr)
            )
            try:
                await mgr.start_all()
            finally:
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass

        try:
            loop.run_until_complete(_run())
        except Exception as e:
            _channel_logger.error(f"Bus thread error: {e}")
        finally:
            loop.close()

    thread = threading.Thread(target=_bus_thread_entry, daemon=True)
    _bus_thread = thread
    thread.start()

    # Wait briefly for the loop to start
    for _ in range(20):
        if _bus_loop is not None:
            break
        time.sleep(0.1)


def _add_channel_to_running_bus(
    channel_type: str,
    config,
    *,
    send_thinking: bool | None = None,
) -> None:
    """Dynamically add a single channel to the already-running bus.

    Raises:
        RuntimeError: If the bus loop or manager is not initialised.
        ValueError: If the channel type is unknown or already registered.
    """
    if not _manager or not _bus_loop:
        raise RuntimeError("Bus not initialised")

    effective_send_thinking = (
        getattr(config, "channel_send_thinking", True)
        if send_thinking is None
        else send_thinking
    )

    async def _do_add():
        channel = await _manager.add_channel(channel_type, config)
        channel.send_thinking = bool(effective_send_thinking)

    future = asyncio.run_coroutine_threadsafe(_do_add(), _bus_loop)
    future.result(timeout=10)


async def _bus_inbound_consumer(bus, manager) -> None:
    """Consume inbound messages from bus and bridge to the main CLI thread.

    This does NOT invoke the agent.  It enqueues a ``ChannelMessage`` on
    the thread-safe queue and waits for the main thread to set a response.
    Once the response arrives it publishes the outbound message on the bus.
    """
    from ..channels.bus.events import OutboundMessage

    while True:
        try:
            msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        _channel_logger.info(
            f"[bus] Received from {msg.channel}:{msg.sender_id}: "
            f"{msg.content[:60]}..."
        )
        manager.record_message(msg.channel, "received")

        channel = manager.get_channel(msg.channel)
        if channel:
            await channel.start_typing(msg.chat_id)

        # Enqueue for main CLI thread to process with its own event loop
        cm = ChannelMessage(
            msg_id=str(uuid.uuid4()),
            content=msg.content,
            sender=msg.sender_id,
            channel_type=msg.channel,
            metadata=msg.metadata,
            channel_ref=channel,
            bus_ref=bus,
            chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
        event = _enqueue_channel_message(cm)

        # Wait (non-blocking for asyncio) until main thread sets response
        _RESPONSE_TIMEOUT = 600  # 10 minutes max per message
        replied = await asyncio.to_thread(event.wait, _RESPONSE_TIMEOUT)
        if not replied:
            _channel_logger.warning(
                f"[bus] Response timeout ({_RESPONSE_TIMEOUT}s) for {cm.msg_id}"
            )
        response = _pop_channel_response(cm.msg_id) or "No response"

        # Publish the response back through the bus → channel
        try:
            await bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=response,
                reply_to=msg.message_id or None,
                metadata=msg.metadata,
            ))
            manager.record_message(msg.channel, "sent")
        except Exception as e:
            _channel_logger.error(f"[bus] Outbound error: {e}")
        finally:
            if channel:
                await channel.stop_typing(msg.chat_id)


def _print_channel_panel(channels: list[tuple[str, bool, str]]) -> None:
    """Print a summary panel for active channels.

    Args:
        channels: List of (name, ok, detail) tuples.
    """
    lines: list[Text] = []
    all_ok = True
    for name, ok, detail in channels:
        line = Text()
        if ok:
            line.append("\u25cf ", style="green")
            line.append(name, style="bold")
        else:
            line.append("\u2717 ", style="yellow")
            line.append(name, style="bold yellow")
            all_ok = False
        if detail:
            line.append(f"  {detail}", style="dim")
        lines.append(line)

    body = Text("\n").join(lines)
    border = "green" if all_ok else "yellow"
    console.print(Panel(body, title="[bold]Channels[/bold]", border_style=border, expand=False))
    console.print()


def _cmd_channel(
    args: str,
    agent: Any,
    thread_id: str,
    *,
    send_thinking: bool | None = None,
) -> None:
    """Start a channel in background using bus mode.

    Usage:
        /channel [telegram|discord|imessage]  -- start channel (default from config)
        /channel status                       -- show current channel status
        /channel stop                         -- stop running channel
    """
    global _cli_agent, _cli_thread_id

    from ..config import load_config
    app_config = load_config()

    channel_type = args.strip().lower() if args and args.strip() else ""
    if channel_type == "status":
        running = _channels_running_list()
        if running and _manager:
            detailed = _manager.get_detailed_status()
            table = Table(title="Channel Status", show_header=True, expand=False)
            table.add_column("Channel", style="cyan")
            table.add_column("Status")
            table.add_column("Uptime", style="dim")
            table.add_column("Rx", justify="right")
            table.add_column("Tx", justify="right")
            for ch_name in running:
                info = detailed.get(ch_name, {})
                secs = info.get("uptime_seconds", 0)
                mins, s = divmod(int(secs), 60)
                hours, mins = divmod(mins, 60)
                uptime = f"{hours}h{mins:02d}m" if hours else f"{mins}m{s:02d}s"
                rx = str(info.get("received", 0))
                tx = str(info.get("sent", 0))
                table.add_row(ch_name, "[green]running[/green]", uptime, rx, tx)
            console.print(table)
            console.print()
        else:
            console.print("[dim]No channel running[/dim]\n")
        return

    if not channel_type:
        channel_type = app_config.channel_enabled
    if not channel_type:
        console.print("[yellow]No channel configured.[/yellow]")
        console.print("[dim]Run[/dim] evosci onboard [dim]or specify:[/dim] /channel telegram\n")
        return

    requested = [t.strip() for t in channel_type.split(",") if t.strip()]

    if _channels_is_running():
        running = _channels_running_list()
        results: list[tuple[str, bool, str]] = []
        for ct in requested:
            if ct in running:
                results.append((ct, True, "already running"))
            else:
                try:
                    _add_channel_to_running_bus(
                        ct,
                        app_config,
                        send_thinking=send_thinking,
                    )
                    results.append((ct, True, "connected (bus)"))
                except Exception as e:
                    results.append((ct, False, str(e)))
        _print_channel_panel(results)
        return

    _cli_agent = agent
    _cli_thread_id = thread_id

    # Override channel_enabled for this invocation
    original = app_config.channel_enabled
    app_config.channel_enabled = channel_type
    try:
        _start_channels_bus_mode(
            app_config,
            agent,
            thread_id,
            send_thinking=send_thinking,
        )
        results = [(ct, True, "connected (bus)") for ct in requested]
    except Exception as e:
        results = [(ct, False, str(e)) for ct in requested]
    finally:
        app_config.channel_enabled = original

    _print_channel_panel(results)


def _cmd_channel_stop(channel_type: str | None = None) -> None:
    """Stop background channel(s).

    Args:
        channel_type: Specific channel to stop, or None to stop all.
    """
    if not _channels_is_running():
        console.print("[dim]No channel running[/dim]\n")
        return
    if channel_type:
        if not _channels_is_running(channel_type):
            console.print(f"[dim]{channel_type} is not running[/dim]\n")
            return
        _channels_stop(channel_type)
        console.print(f"[dim]{channel_type} stopped[/dim]\n")
    else:
        running = _channels_running_list()
        _channels_stop()
        console.print(f"[dim]{', '.join(running)} stopped[/dim]\n")


def _auto_start_channel(
    agent: Any,
    thread_id: str,
    config,
    *,
    send_thinking: bool | None = None,
) -> None:
    """Start channels automatically from config (bus mode).

    Args:
        agent: Compiled agent graph.
        thread_id: Current thread ID.
        config: EvoScientistConfig with channel settings.
    """
    global _cli_agent, _cli_thread_id

    if not config.channel_enabled:
        return

    _cli_agent = agent
    _cli_thread_id = thread_id

    _start_channels_bus_mode(
        config,
        agent,
        thread_id,
        send_thinking=send_thinking,
    )
    types = [t.strip() for t in config.channel_enabled.split(",") if t.strip()]
    results = [(ct, True, "connected (bus)") for ct in types]
    _print_channel_panel(results)
