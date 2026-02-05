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
from typing import Callable

from . import IMessageChannel, IMessageConfig
from ..base import OutgoingMessage

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_agent_handler(on_thinking: Callable | None = None):
    """Create handler that uses EvoScientist agent.

    Args:
        on_thinking: Optional async callback for thinking content.
            Signature: async def on_thinking(sender: str, thinking: str) -> None
    """
    from langchain_core.messages import HumanMessage
    from ...EvoScientist import create_cli_agent
    from ...stream.events import stream_agent_events

    agent = create_cli_agent()
    sessions: dict[str, str] = {}  # sender -> thread_id

    async def handler(msg) -> str:
        import uuid
        sender = msg.sender
        if sender not in sessions:
            sessions[sender] = str(uuid.uuid4())
        thread_id = sessions[sender]

        if on_thinking:
            final_content = ""
            thinking_buffer = []

            async for event in stream_agent_events(agent, msg.content, thread_id):
                event_type = event.get("type")

                if event_type == "thinking":
                    thinking_text = event.get("content", "")
                    if thinking_text:
                        thinking_buffer.append(thinking_text)
                        if len("".join(thinking_buffer)) > 200:
                            await on_thinking(sender, "".join(thinking_buffer), msg.metadata)
                            thinking_buffer.clear()

                elif event_type == "text":
                    final_content += event.get("content", "")

                elif event_type == "done":
                    final_content = event.get("content", "") or final_content

            if thinking_buffer:
                await on_thinking(sender, "".join(thinking_buffer), msg.metadata)

            return final_content or "No response"
        else:
            config = {"configurable": {"thread_id": thread_id}}
            result = agent.invoke(
                {"messages": [HumanMessage(content=msg.content)]},
                config=config,
            )
            messages = result.get("messages", [])
            for m in reversed(messages):
                if hasattr(m, "content") and m.type == "ai":
                    content = m.content
                    # Handle structured content (thinking mode)
                    if isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        return "\n".join(text_parts) if text_parts else "No response"
                    # Handle plain string content
                    return content
            return "No response"

    return handler


class IMessageServer:
    """Server that runs the iMessage channel and handles messages."""

    def __init__(
        self,
        config: IMessageConfig,
        handler: Callable | None = None,
        send_thinking: bool = False,
        debounce_window: float = 1.0,
    ):
        """Initialize iMessage server.

        Args:
            config: iMessage channel configuration.
            handler: Message handler function. If None, uses echo handler.
            send_thinking: If True, send thinking content as intermediate messages.
            debounce_window: Time window (seconds) to wait for additional messages
                before processing. Messages from same sender within this window
                are merged. Default 1.0s.
        """
        self.config = config
        self.channel = IMessageChannel(config)
        self.send_thinking = send_thinking
        self.debounce_window = debounce_window
        self._running = False
        self._pending_thinking: dict[str, str] = {}  # sender -> accumulated thinking

        # Message buffering for debounce
        self._message_buffers: dict[str, list[str]] = {}  # sender -> [messages]
        self._message_metadata: dict[str, dict] = {}  # sender -> metadata (from first message)
        self._debounce_tasks: dict[str, asyncio.Task] = {}  # sender -> pending task
        self._processing: set[str] = set()  # senders currently being processed

        if handler:
            self.handler = handler
        else:
            self.handler = self._default_handler

    async def _default_handler(self, msg) -> str:
        """Default echo handler."""
        return f"Echo: {msg.content}"

    async def _process_buffered_messages(self, sender: str) -> None:
        """Process all buffered messages for a sender."""
        if sender not in self._message_buffers:
            return

        messages = self._message_buffers.pop(sender, [])
        metadata = self._message_metadata.pop(sender, None)
        self._debounce_tasks.pop(sender, None)

        if not messages:
            return

        merged_content = "\n".join(messages)
        logger.info(f"Processing {len(messages)} merged message(s) from {sender}")
        logger.debug(f"Using metadata: {metadata}")

        self._processing.add(sender)
        try:
            class MergedMessage:
                def __init__(self, s, c, m):
                    self.sender = s
                    self.content = c
                    self.metadata = m

            merged_msg = MergedMessage(sender, merged_content, metadata)
            logger.debug(f"Calling handler with metadata: {metadata}")
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
            # Save metadata from first message (contains chat_id/chat_guid for replies)
            self._message_metadata[sender] = msg.metadata
            logger.debug(f"Saved metadata for {sender}: {msg.metadata}")
        self._message_buffers[sender].append(msg.content)

        if sender in self._debounce_tasks:
            self._debounce_tasks[sender].cancel()

        async def debounce_callback():
            await asyncio.sleep(self.debounce_window)
            await self._process_buffered_messages(sender)

        self._debounce_tasks[sender] = asyncio.create_task(debounce_callback())

    async def send_thinking_message(self, sender: str, thinking: str, metadata: dict | None = None) -> None:
        """Send thinking content as intermediate message."""
        if not self.send_thinking:
            return

        logger.debug(f"Sending thinking to {sender} with metadata: {metadata}")
        content = f"[Thinking...]\n{thinking}"
        await self.channel.send(OutgoingMessage(
            recipient=sender,
            content=content,
            metadata=metadata or {},
        ))
        logger.debug(f"Sent thinking to {sender}: {thinking[:50]}...")

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
        "--thinking",
        action="store_true",
        help="Send thinking content as intermediate messages (requires --agent)",
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
    send_thinking = args.thinking and args.agent

    if args.agent:
        logger.info("Loading EvoScientist agent...")
        logger.info("Agent loaded")

    server = IMessageServer(
        config,
        handler=None,
        send_thinking=send_thinking,
        debounce_window=args.debounce,
    )

    if args.agent:
        on_thinking = server.send_thinking_message if send_thinking else None
        handler = create_agent_handler(on_thinking=on_thinking)
        server.handler = handler
        if send_thinking:
            logger.info("Thinking messages enabled")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(server.stop()))

    await server.run()


def main():
    """Entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
