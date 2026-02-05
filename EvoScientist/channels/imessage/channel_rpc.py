"""iMessage channel using imsg JSON-RPC.

This is an improved implementation that uses the imsg CLI
via JSON-RPC, similar to OpenClaw's approach.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator

from ..base import Channel, IncomingMessage, OutgoingMessage, ChannelError
from .rpc_client import ImsgRpcClient, RpcNotification
from .targets import (
    normalize_handle,
    parse_target,
    ChatIdTarget,
    ChatGuidTarget,
    ChatIdentifierTarget,
)

logger = logging.getLogger(__name__)


@dataclass
class IMessageConfig:
    """Configuration for iMessage channel."""

    cli_path: str = "imsg"
    db_path: str | None = None
    allowed_senders: list[str] = field(default_factory=list)
    include_attachments: bool = False
    text_chunk_limit: int = 4000
    service: str = "auto"  # imessage, sms, or auto
    region: str = "US"


class IMessageChannelRpc(Channel):
    """iMessage channel using imsg JSON-RPC.

    This implementation uses the imsg CLI via JSON-RPC over stdio,
    providing real-time message streaming instead of polling.

    Args:
        config: Channel configuration
    """

    def __init__(self, config: IMessageConfig | None = None):
        self.config = config or IMessageConfig()
        self._client: ImsgRpcClient | None = None
        self._running = False
        self._message_queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._subscription_id: int | None = None

    def _handle_notification(self, notification: RpcNotification) -> None:
        """Handle incoming RPC notifications."""
        if notification.method == "message":
            self._handle_message(notification.params)
        elif notification.method == "error":
            logger.error(f"imsg error: {notification.params}")

    def _handle_message(self, params: dict | None) -> None:
        """Process incoming message notification."""
        if not params:
            return

        message = params.get("message", {})
        if not message:
            return

        # Skip messages from self
        if message.get("is_from_me"):
            return

        sender = message.get("sender", "").strip()
        if not sender:
            return

        # Check allowed senders
        chat_id = message.get("chat_id")
        chat_guid = message.get("chat_guid")
        if not self._is_sender_allowed(sender, chat_id, chat_guid):
            logger.debug(f"Ignoring message from {sender}")
            return

        text = message.get("text", "").strip()
        if not text:
            return

        # Parse timestamp
        timestamp = datetime.now()
        if created_at := message.get("created_at"):
            try:
                timestamp = datetime.fromisoformat(created_at)
            except ValueError:
                pass

        # Build metadata
        metadata = {
            "chat_id": message.get("chat_id"),
            "chat_guid": message.get("chat_guid"),
            "is_group": message.get("is_group", False),
            "chat_name": message.get("chat_name"),
        }

        # Handle attachments if enabled
        if self.config.include_attachments:
            attachments = message.get("attachments", [])
            if attachments:
                metadata["attachments"] = attachments

        incoming = IncomingMessage(
            sender=sender,
            content=text,
            timestamp=timestamp,
            message_id=str(message.get("id", "")),
            metadata=metadata,
        )

        try:
            self._message_queue.put_nowait(incoming)
        except asyncio.QueueFull:
            logger.warning("Message queue full, dropping message")

    def _is_sender_allowed(
        self,
        sender: str,
        chat_id: int | None = None,
        chat_guid: str | None = None,
    ) -> bool:
        """Check if sender is in allowed list.

        Supports:
        - Wildcard "*" to allow all
        - chat_id:123 to match by chat ID
        - chat_guid:abc to match by chat GUID
        - Normalized phone/email matching
        """
        if not self.config.allowed_senders:
            return True

        # Wildcard allows all
        if "*" in self.config.allowed_senders:
            return True

        sender_normalized = normalize_handle(sender)

        for entry in self.config.allowed_senders:
            entry = entry.strip()
            if not entry:
                continue

            lower = entry.lower()

            # Check chat_id match
            if lower.startswith("chat_id:") or lower.startswith("chatid:"):
                if chat_id is not None:
                    try:
                        allowed_id = int(entry.split(":", 1)[1].strip())
                        if allowed_id == chat_id:
                            return True
                    except ValueError:
                        pass
                continue

            # Check chat_guid match
            if lower.startswith("chat_guid:") or lower.startswith("chatguid:"):
                if chat_guid:
                    allowed_guid = entry.split(":", 1)[1].strip()
                    if allowed_guid == chat_guid:
                        return True
                continue

            # Normalize and compare handle
            entry_normalized = normalize_handle(entry)
            if entry_normalized == sender_normalized:
                return True

        return False

    def add_allowed_sender(self, sender: str) -> None:
        """Add a sender to the allowed list."""
        normalized = normalize_handle(sender) if not sender.startswith("chat") else sender
        if normalized not in self.config.allowed_senders:
            self.config.allowed_senders.append(normalized)
            logger.info(f"Added allowed sender: {normalized}")

    def remove_allowed_sender(self, sender: str) -> None:
        """Remove a sender from the allowed list."""
        normalized = normalize_handle(sender) if not sender.startswith("chat") else sender
        if normalized in self.config.allowed_senders:
            self.config.allowed_senders.remove(normalized)
            logger.info(f"Removed allowed sender: {normalized}")

    def clear_allowed_senders(self) -> None:
        """Clear allowed list (allow all)."""
        self.config.allowed_senders = []
        logger.info("Cleared allowed senders (allowing all)")

    def list_allowed_senders(self) -> list[str]:
        """Get current allowed senders."""
        return self.config.allowed_senders

    async def start(self) -> None:
        """Initialize and start the channel."""
        logger.info("Starting iMessage channel (RPC)...")

        self._client = ImsgRpcClient(
            cli_path=self.config.cli_path,
            db_path=self.config.db_path,
            on_notification=self._handle_notification,
        )

        try:
            await self._client.start()
        except Exception as e:
            raise ChannelError(f"Failed to start imsg: {e}") from e

        # Subscribe to message events
        try:
            result = await self._client.request(
                "watch.subscribe",
                {"attachments": self.config.include_attachments},
            )
            self._subscription_id = result.get("subscription")
        except Exception as e:
            await self._client.stop()
            raise ChannelError(f"Failed to subscribe: {e}") from e

        self._running = True
        logger.info("iMessage channel started")

    async def stop(self) -> None:
        """Stop the channel and clean up."""
        logger.info("Stopping iMessage channel...")
        self._running = False

        if self._client and self._subscription_id:
            try:
                await self._client.request(
                    "watch.unsubscribe",
                    {"subscription": self._subscription_id},
                )
            except Exception:
                pass

        if self._client:
            await self._client.stop()
            self._client = None

        logger.info("iMessage channel stopped")

    async def receive(self) -> AsyncIterator[IncomingMessage]:
        """Yield incoming messages from the queue."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0,
                )
                yield msg
            except asyncio.TimeoutError:
                continue

    def _segment_message(self, content: str) -> list[str]:
        """Split long message into segments."""
        limit = self.config.text_chunk_limit
        if len(content) <= limit:
            return [content]

        segments = []
        remaining = content

        while remaining:
            if len(remaining) <= limit:
                segments.append(remaining)
                break

            chunk = remaining[:limit]
            # Try split at newline
            nl_pos = chunk.rfind("\n")
            if nl_pos > limit // 2:
                split_pos = nl_pos + 1
            else:
                # Try split at space
                sp_pos = chunk.rfind(" ")
                if sp_pos > limit // 2:
                    split_pos = sp_pos + 1
                else:
                    split_pos = limit

            segments.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()

        return segments

    async def send(self, message: OutgoingMessage) -> bool:
        """Send a message via iMessage."""
        if not self._client:
            logger.error("Cannot send: client not running")
            return False

        segments = self._segment_message(message.content)

        for segment in segments:
            params = self._build_send_params(message, segment)
            if not params:
                logger.error(f"_build_send_params returned None for recipient={message.recipient}, metadata={message.metadata}")
                return False

            try:
                logger.debug(f"Calling imsg send with params: {params}")
                await self._client.request("send", params)
            except Exception as e:
                logger.error(f"Send failed: {e}")
                logger.error(f"Failed params were: {params}")
                return False

        return True

    def _build_send_params(
        self, message: OutgoingMessage, text: str
    ) -> dict | None:
        """Build send parameters from message."""
        params: dict = {
            "text": text,
            "service": self.config.service,
            "region": self.config.region,
        }

        logger.debug(f"Building send params - recipient: {message.recipient}, metadata: {message.metadata}")

        # Check metadata for chat targets
        chat_id = message.metadata.get("chat_id")
        chat_guid = message.metadata.get("chat_guid")
        chat_identifier = message.metadata.get("chat_identifier")

        if chat_id:
            params["chat_id"] = chat_id
        elif chat_guid:
            params["chat_guid"] = chat_guid
        elif chat_identifier:
            params["chat_identifier"] = chat_identifier
        elif message.recipient:
            # Parse recipient to determine target type
            try:
                target = parse_target(message.recipient)
                if isinstance(target, ChatIdTarget):
                    params["chat_id"] = target.chat_id
                elif isinstance(target, ChatGuidTarget):
                    params["chat_guid"] = target.chat_guid
                elif isinstance(target, ChatIdentifierTarget):
                    params["chat_identifier"] = target.chat_identifier
                else:
                    params["to"] = target.to
                    params["service"] = target.service.value
            except ValueError:
                params["to"] = message.recipient
        else:
            logger.error("Cannot send: no recipient or chat target")
            return None

        logger.debug(f"Built send params: {params}")
        return params

    async def send_media(
        self,
        recipient: str,
        file_path: str,
        caption: str = "",
        metadata: dict | None = None,
    ) -> bool:
        """Send a media file via iMessage.

        Args:
            recipient: Target recipient or chat target
            file_path: Local path to the media file
            caption: Optional caption text
            metadata: Optional metadata with chat_id etc.

        Returns:
            True if sent successfully
        """
        if not self._client:
            logger.error("Cannot send media: client not running")
            return False

        metadata = metadata or {}
        params: dict = {
            "file": file_path,
            "service": self.config.service,
            "region": self.config.region,
        }

        if caption:
            params["text"] = caption

        # Determine target
        chat_id = metadata.get("chat_id")
        chat_guid = metadata.get("chat_guid")

        if chat_id:
            params["chat_id"] = chat_id
        elif chat_guid:
            params["chat_guid"] = chat_guid
        elif recipient:
            try:
                target = parse_target(recipient)
                if isinstance(target, ChatIdTarget):
                    params["chat_id"] = target.chat_id
                elif isinstance(target, ChatGuidTarget):
                    params["chat_guid"] = target.chat_guid
                else:
                    params["to"] = target.to
            except ValueError:
                params["to"] = recipient
        else:
            logger.error("Cannot send media: no recipient")
            return False

        try:
            await self._client.request("send", params)
            return True
        except Exception as e:
            logger.error(f"Send media failed: {e}")
            return False
