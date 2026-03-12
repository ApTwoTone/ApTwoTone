"""
Notification Service — unified wrapper around Telegram + SSE broadcast.
Services call this instead of knowing about bot/broadcast internals.
"""


class NotificationService:
    def __init__(self, notify_fn=None, broadcast_fn=None):
        self._notify = notify_fn
        self._broadcast = broadcast_fn

    async def notify(self, message: str):
        if self._notify:
            try:
                await self._notify(message)
            except Exception:
                pass

    async def broadcast(self, event: dict):
        if self._broadcast:
            try:
                await self._broadcast(event)
            except Exception:
                pass

    async def notify_and_broadcast(self, message: str, event: dict):
        await self.notify(message)
        await self.broadcast(event)
