import asyncio
import base64
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus

from aiohttp import web
from cactus_schema.notification import CollectedHeader, CollectedNotification

from cactus_client_notifications.server.time import utc_now

logger = logging.getLogger(__name__)


class NotificationException(Exception):  # noqa: N818
    status_code: HTTPStatus  # What status code should be served by whatever is upstream of this request?

    def __init__(self, status_code: HTTPStatus, *args: object) -> None:
        self.status_code = status_code
        super().__init__(*args)


def generate_unique_id() -> str:
    """Generates a URI safe string with a random value"""
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode()


async def generate_collected_notification(request: web.Request) -> CollectedNotification:
    """Generates a CollectedNotification from an incoming web request"""

    headers = [CollectedHeader(name=name, value=val) for name, val in request.headers.items()]
    if request.body_exists:
        body = await request.text()
    else:
        body = ""

    return CollectedNotification(
        method=request.method, headers=headers, body=body, received_at=utc_now(), remote=request.remote
    )


class EndpointData:
    notifications: list[CollectedNotification]
    enabled: bool  # Is this endpoint enabled?
    created_at: datetime  # When was this endpoint created?
    interacted_at: datetime  # When did the last notification get received / endpoint get touched

    def __init__(self) -> None:
        self.notifications = []
        self.enabled = True
        self.interacted_at = utc_now()
        self.created_at = utc_now()


@dataclass
class EndpointMetadata:
    """Basic metadata about an Endpoint in the EndpointStore"""

    endpoint_id: str
    total_notifications: int
    enabled: bool  # Is this endpoint enabled?
    created_at: datetime  # When was this endpoint created?
    interacted_at: datetime  # When did the last notification get received / endpoint get touched


class EndpointStore:
    lock: asyncio.Lock
    max_active_endpoints: int
    max_endpoint_notifications: int

    _store: dict[str, EndpointData]

    def __init__(self, max_active_endpoints: int, max_endpoint_notifications: int) -> None:
        self.lock = asyncio.Lock()
        self.max_active_endpoints = max_active_endpoints
        self.max_endpoint_notifications = max_endpoint_notifications
        self._store = {}

    async def create_endpoint(self) -> str:
        """Creates a new endpoint with a unique ID. Returns that ID

        Can raise NotificationException"""
        async with self.lock:
            current_endpoint_count = len(self._store)
            if current_endpoint_count >= self.max_active_endpoints:
                raise NotificationException(
                    HTTPStatus.INSUFFICIENT_STORAGE,
                    f"There are already {current_endpoint_count} endpoints and max is {self.max_active_endpoints}",
                )

            new_id = generate_unique_id()
            if new_id in self._store:
                raise NotificationException(
                    HTTPStatus.INTERNAL_SERVER_ERROR, f"ID generation collision. '{new_id}' already exists"
                )

            logger.info(f"Created endpoint {new_id}")
            self._store[new_id] = EndpointData()
            return new_id

    async def update_endpoint(self, endpoint_id: str, enabled: bool) -> None:
        """Tries to update settings for endpoint_id. Raises NotificationException if this can't be done"""
        async with self.lock:
            data = self._store.get(endpoint_id, None)
            if data is None:
                raise NotificationException(
                    HTTPStatus.NOT_FOUND, f"No endpoint with ID {endpoint_id} exists (or it's been removed)."
                )

            data.enabled = enabled
            data.interacted_at = utc_now()
            logger.info(f"Updated endpoint {endpoint_id} - enabled={enabled}")

    async def try_delete_endpoint(self, endpoint_id: str) -> bool:
        """Tries to delete endpoint_id - returns True if it exists and was deleted. False if it DNE"""
        async with self.lock:
            exists = endpoint_id in self._store
            if exists:
                logger.info(f"Deleted endpoint {endpoint_id}")
                del self._store[endpoint_id]

            return exists

    async def add_notification(self, endpoint_id: str, notification: CollectedNotification) -> None:
        """Adds a notification to the specified endpoint_id - raises NotificationException if this can't be done."""
        async with self.lock:
            data = self._store.get(endpoint_id, None)
            if data is None:
                raise NotificationException(
                    HTTPStatus.NOT_FOUND, f"No endpoint with ID {endpoint_id} exists (or it's been removed)."
                )

            if not data.enabled:
                raise NotificationException(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"Endpoint {endpoint_id} is temporarily disabled to simulate an outage.",
                )

            if len(data.notifications) >= self.max_endpoint_notifications:
                raise NotificationException(
                    HTTPStatus.INSUFFICIENT_STORAGE,
                    f"Endpoint {endpoint_id} has exceeded the max notifications ({self.max_endpoint_notifications})",
                )

            data.interacted_at = utc_now()
            data.notifications.append(notification)

            logger.info(
                f"Added {notification.method} notification ({len(notification.body)} bytes) to endpoint {endpoint_id}"
            )

    async def collect_notifications(self, endpoint_id: str) -> list[CollectedNotification]:
        """Collects all notifications for the specified endpoint_id. Notifications will be "consumed".

        Raises NotificationException on error."""
        async with self.lock:
            data = self._store.get(endpoint_id, None)
            if data is None:
                raise NotificationException(
                    HTTPStatus.NOT_FOUND, f"No endpoint with ID {endpoint_id} exists (or it's been removed)."
                )

            collected_notifications = data.notifications
            data.interacted_at = utc_now()
            data.notifications = []

            logger.info(f"Collected {len(collected_notifications)} notifications from endpoint {endpoint_id}")
            return collected_notifications

    async def cleanup_expired_endpoints(self, now: datetime, max_idle: timedelta, max_duration: timedelta) -> None:
        """Enumerates all endpoints, removing any that have reached their max duration/idle time"""
        async with self.lock:
            expired_endpoint_ids: list[str] = []
            for endpoint_id, data in self._store.items():
                current_duration = now - data.created_at
                if current_duration > max_duration:
                    logger.info(
                        f"Endpoint {endpoint_id} duration {current_duration} has exceeded max duration {max_duration}"
                    )
                    expired_endpoint_ids.append(endpoint_id)
                    continue

                current_idle = now - data.interacted_at
                if current_idle > max_idle:
                    logger.info(f"Endpoint {endpoint_id} idle time {current_idle} has exceeded max idle {max_idle}")
                    expired_endpoint_ids.append(endpoint_id)
                    continue

            for endpoint_id in expired_endpoint_ids:
                logger.info(f"Cleanup has deleted endpoint {endpoint_id}")
                del self._store[endpoint_id]

    async def get_endpoint_metadata(self) -> list[EndpointMetadata]:
        async with self.lock:
            return [
                EndpointMetadata(
                    endpoint_id,
                    len(endpoint.notifications),
                    endpoint.enabled,
                    endpoint.created_at,
                    endpoint.interacted_at,
                )
                for endpoint_id, endpoint in self._store.items()
            ]
