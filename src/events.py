from typing import Callable, Dict, Any
import threading
import logging

logger = logging.getLogger(__name__)
class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: Callable):
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            callback_name = getattr(callback, "__qualname__", str(callback))
            logger.debug(
                f"Subscribed to event type: {event_type} with callback: {callback_name}"
            )

    def publish(self, event_type: str, data: Dict[str, Any]):
        with self._lock:
            subscribers = self._subscribers.get(event_type, []).copy()

        if not subscribers:
            logger.debug(f"No subscribers for {event_type}")
            return

        logger.debug(f"Emitting event: {event_type} with data: {data}")

        for callback in subscribers:
            try:
                callback(data)
            except Exception as e:
                cb_name = getattr(callback, "__qualname__", str(callback))
                logger.error(
                    f"Error in subscriber {cb_name} for event {event_type}: {e}"
                )

# Singleton instance
bus = EventBus()


class Event:
    SERVER_STARTED = "SERVER_STARTED"
    SERVER_STOPPED = "SERVER_STOPPED"
    PLAYER_JOINED = "PLAYER_JOINED"
    PLAYER_LEFT = "PLAYER_LEFT"
    BAN_ADDED = "BAN_ADDED"
    BAN_REMOVED = "BAN_REMOVED"

    # Status events
    SERVER_STATUS = "SERVER_STATUS"
    STEAM_UPDATE_STATUS = "STEAM_UPDATE_STATUS"
    # Command events
    CMD_START_SERVER = "CMD_START_SERVER"
    CMD_STOP_SERVER = "CMD_STOP_SERVER"
    CMD_KICK_PLAYER = "CMD_KICK_PLAYER"
    CMD_BAN_PLAYER = "CMD_BAN_PLAYER"
    CMD_UNBAN_PLAYER = "CMD_UNBAN_PLAYER"
