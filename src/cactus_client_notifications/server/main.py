import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncGenerator
from datetime import timedelta

from aiohttp import web
from cactus_schema.notification import uri

import cactus_client_notifications.server.shared as shared
from cactus_client_notifications.server import handler
from cactus_client_notifications.server.endpoint_store import EndpointStore
from cactus_client_notifications.server.settings import ServerSettings, ServerStats
from cactus_client_notifications.server.time import utc_now

logger = logging.getLogger(__name__)


async def periodic_task(app: web.Application) -> None:
    """Periodic task called app[APPKEY_PERIOD_SEC]

    Args:
        app (web.Application): The AIOHTTP application instance.
    """
    server_settings = app[shared.APPKEY_SERVER_SETTINGS]
    store = app[shared.APPKEY_NOTIFICATION_STORE]

    while True:
        # Sleep first - we don't need to initiate a cleanup immediately
        await asyncio.sleep(server_settings.cleanup_frequency.total_seconds())

        try:
            await store.cleanup_expired_endpoints(
                utc_now(), server_settings.max_endpoint_idle_duration, server_settings.max_endpoint_duration
            )
        except Exception as exc:
            # Catch and log uncaught exceptions to prevent periodic task from hanging
            logger.error("Uncaught exception in periodic task", exc_info=exc)


async def setup_periodic_task(app: web.Application) -> AsyncGenerator:
    """Setup periodic task.

    The periodic task is accessible through app[APPKEY_PERIODIC_TASKS].
    The code for the task is defined in the function 'periodic_task'.
    """
    app[shared.APPKEY_PERIODIC_TASK] = asyncio.create_task(periodic_task(app))

    yield

    app[shared.APPKEY_PERIODIC_TASK].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app[shared.APPKEY_PERIODIC_TASK]


def create_app() -> web.Application:

    app = web.Application()

    # Check env for basic settings
    ENV_APP_PORT = int(os.getenv("APP_PORT", 8080))
    ENV_PUBLIC_SERVER_URL = os.getenv("SERVER_URL", f"http://localhost:{ENV_APP_PORT}")
    ENV_MOUNT_POINT = os.getenv("MOUNT_POINT", "/")
    ENV_MAX_IDLE_DURATION_SECONDS = float(os.getenv("MAX_IDLE_DURATION_SECONDS", 3600))
    ENV_MAX_DURATION_SECONDS = float(os.getenv("MAX_DURATION_SECONDS", (3600 * 24 * 3) + 3600))
    ENV_MAX_ACTIVE_ENDPOINTS = int(os.getenv("MAX_ACTIVE_ENDPOINTS", 1024))
    ENV_MAX_ENDPOINT_NOTIFICATIONS = int(os.getenv("MAX_ENDPOINT_NOTIFICATIONS", 100))
    ENV_CLEANUP_FREQUENCY_SECONDS = float(os.getenv("CLEANUP_FREQUENCY_SECONDS", 120))
    server_settings = ServerSettings(
        port=ENV_APP_PORT,
        public_server_url=ENV_PUBLIC_SERVER_URL,
        mount_point=ENV_MOUNT_POINT,
        max_endpoint_idle_duration=timedelta(seconds=ENV_MAX_IDLE_DURATION_SECONDS),
        max_endpoint_duration=timedelta(seconds=ENV_MAX_DURATION_SECONDS),
        cleanup_frequency=timedelta(seconds=ENV_CLEANUP_FREQUENCY_SECONDS),
        started_at=utc_now(),
        max_active_endpoints=ENV_MAX_ACTIVE_ENDPOINTS,
        max_endpoint_notifications=ENV_MAX_ENDPOINT_NOTIFICATIONS,
    )

    app[shared.APPKEY_NOTIFICATION_STORE] = EndpointStore(
        max_endpoint_notifications=server_settings.max_endpoint_notifications,
        max_active_endpoints=server_settings.max_active_endpoints,
    )
    app[shared.APPKEY_SERVER_SETTINGS] = server_settings
    app[shared.APPKEY_SERVER_STATS] = ServerStats()

    # Add routes for Test Runner
    mount = server_settings.mount_point
    app.router.add_route(
        "POST", handler.path_join(mount, uri.URI_MANAGE_ENDPOINT_LIST), handler.post_manage_endpoint_list
    )
    app.router.add_route("GET", handler.path_join(mount, uri.URI_MANAGE_ENDPOINT), handler.get_manage_endpoint)
    app.router.add_route("PUT", handler.path_join(mount, uri.URI_MANAGE_ENDPOINT), handler.put_manage_endpoint)
    app.router.add_route("DELETE", handler.path_join(mount, uri.URI_MANAGE_ENDPOINT), handler.delete_manage_endpoint)
    app.router.add_route("*", handler.path_join(mount, uri.URI_ENDPOINT), handler.webhook_endpoint)
    app.router.add_route("GET", handler.path_join(mount, uri.URI_MANAGE_SERVER), handler.get_manage_server)

    # Start the periodic task
    app.cleanup_ctx.append(setup_periodic_task)

    return app


def create_app_with_logging() -> web.Application:
    ENV_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    logging.basicConfig(
        level=ENV_LOG_LEVEL,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] (%(funcName)s): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    app = create_app()

    return app


app = create_app_with_logging()
if __name__ == "__main__":
    web.run_app(app, port=app[shared.APPKEY_SERVER_SETTINGS].port)
