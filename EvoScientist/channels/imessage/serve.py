"""iMessage channel server.

Standalone script to run the iMessage channel with CLI options.

Usage:
    python -m EvoScientist.channels.imessage.serve [OPTIONS]

Examples:
    # Allow all senders (default)
    python -m EvoScientist.channels.imessage.serve

    # Only allow specific senders
    python -m EvoScientist.channels.imessage.serve --allow +1234567890 --allow user@example.com

    # Custom imsg path
    python -m EvoScientist.channels.imessage.serve --cli-path /usr/local/bin/imsg
"""

import asyncio
import argparse
import logging
import signal
import sys
from typing import Callable

from . import IMessageChannel, IMessageConfig
from ..base import OutgoingMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_agent_handler():
    """Create handler that uses EvoScientist agent."""
    from langchain_core.messages import HumanMessage
    from ...EvoScientist import create_cli_agent

    agent = create_cli_agent()
    sessions: dict[str, str] = {}  # sender -> thread_id

    async def handler(msg) -> str:
        import uuid
        sender = msg.sender
        if sender not in sessions:
            sessions[sender] = str(uuid.uuid4())
        thread_id = sessions[sender]

        config = {"configurable": {"thread_id": thread_id}}
        result = agent.invoke(
            {"messages": [HumanMessage(content=msg.content)]},
            config=config,
        )
        # Extract last AI message
        messages = result.get("messages", [])
        for m in reversed(messages):
            if hasattr(m, "content") and m.type == "ai":
                return m.content
        return "No response"

    return handler


class IMessageServer:
    """Server that runs the iMessage channel and handles messages."""

    def __init__(
        self,
        config: IMessageConfig,
        handler: Callable | None = None,
        debounce_window: float = 1.0,
    ):
        """Initialize iMessage server.

        Args:
            config: iMessage channel configuration.
            handler: Message handler function. If None, uses echo handler.
            debounce_window: Time window (seconds) to wait for additional messages
                before processing. Messages from same sender within this window
                are merged. Default 1.0s.
        """
        self.config = config
        self.channel = IMessageChannel(config)
        self.debounce_window = debounce_window
        self._running = False

        # Message buffering for debounce
        self._message_buffers: dict[str, list[str]] = {}  # sender -> [messages]
        self._debounce_tasks: dict[str, asyncio.Task] = {}  # sender -> pending task
        self._processing: set[str] = set()  # senders currently being processed

        if handler:
            self.handler = handler
        else:
            self.handler = self._default_handler

    async def _default_handler(self, msg) -> str:
        """Default echo handler."""
        return f"Echo: {msg.content}"

    async def _process_buffered_messages(self, sender: str, metadata: dict | None) -> None:
        """Process all buffered messages for a sender."""
        if sender not in self._message_buffers:
            return

        messages = self._message_buffers.pop(sender, [])
        self._debounce_tasks.pop(sender, None)

        if not messages:
            return

        merged_content = "\n".join(messages)
        logger.info(f"Processing {len(messages)} merged message(s) from {sender}")

        self._processing.add(sender)
        try:
            class MergedMessage:
                def __init__(self, s, c, m):
                    self.sender = s
                    self.content = c
                    self.metadata = m

            merged_msg = MergedMessage(sender, merged_content, metadata)
            response = await self.handler(merged_msg)

            if response:
                await self.channel.send(OutgoingMessage(
                    recipient=sender,
                    content=response,
                    metadata=metadata,
                ))
        except Exception as e:
            logger.error(f"Handler error: {e}")
        finally:
            self._processing.discard(sender)

    async def _queue_message(self, msg) -> None:
        """Queue a message for debounced processing."""
        sender = msg.sender

        if sender not in self._message_buffers:
            self._message_buffers[sender] = []
        self._message_buffers[sender].append(msg.content)

        if sender in self._debounce_tasks:
            self._debounce_tasks[sender].cancel()

        async def debounce_callback():
            await asyncio.sleep(self.debounce_window)
            await self._process_buffered_messages(sender, msg.metadata)

        self._debounce_tasks[sender] = asyncio.create_task(debounce_callback())

    async def run(self) -> None:
        """Run the server."""
        await self.channel.start()
        self._running = True

        logger.info("iMessage server running. Press Ctrl+C to stop.")
        if self.config.allowed_senders:
            logger.info(f"Allowed senders: {self.config.allowed_senders}")
        else:
            logger.info("Allowing all senders")
        if self.debounce_window > 0:
            logger.info(f"Message debounce: {self.debounce_window}s")

        try:
            async for msg in self.channel.receive():
                logger.info(f"From {msg.sender}: {msg.content[:50]}...")
                await self._queue_message(msg)
        finally:
            for task in self._debounce_tasks.values():
                task.cancel()
            await self.channel.stop()

    async def stop(self) -> None:
        """Stop the server."""
        self._running = False
        await self.channel.stop()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="iMessage channel server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--allow",
        action="append",
        dest="allowed_senders",
        help="Allowed sender (phone/email). Can be used multiple times.",
    )
    parser.add_argument(
        "--cli-path",
        default="imsg",
        help="Path to imsg CLI (default: imsg)",
    )
    parser.add_argument(
        "--db-path",
        help="Path to Messages database",
    )
    parser.add_argument(
        "--attachments",
        action="store_true",
        help="Include attachments in messages",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Use EvoScientist agent as handler (default: echo)",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Message debounce window in seconds (default: 1.0)",
    )
    return parser.parse_args()


async def async_main():
    """Async entry point."""
    args = parse_args()

    config = IMessageConfig(
        cli_path=args.cli_path,
        db_path=args.db_path,
        allowed_senders=set(args.allowed_senders) if args.allowed_senders else None,
        include_attachments=args.attachments,
    )

    handler = None
    if args.agent:
        logger.info("Loading EvoScientist agent...")
        handler = create_agent_handler()
        logger.info("Agent loaded")

    server = IMessageServer(
        config,
        handler=handler,
        debounce_window=args.debounce,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(server.stop()))

    await server.run()


def main():
    """Entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
