import asyncio
from http import HTTPStatus

import pytest
from aiohttp import ClientSession
from cactus_schema.notification import (
    CollectEndpointResponse,
    ConfigureEndpointRequest,
    CreateEndpointResponse,
)


async def create_endpoint(client_session: ClientSession, endpoint: str) -> CreateEndpointResponse:
    result = await client_session.post(endpoint)
    assert result.status == HTTPStatus.CREATED
    assert result.content_type == "application/json"
    response = CreateEndpointResponse.from_json(await result.text())

    assert response.endpoint_id in response.fully_qualified_endpoint
    assert response.fully_qualified_endpoint.startswith("http://") or response.fully_qualified_endpoint.startswith(
        "https://"
    )
    return response


async def collect_endpoint(client_session: ClientSession, endpoint: str) -> CollectEndpointResponse:
    result = await client_session.get(endpoint)
    assert result.status == HTTPStatus.OK
    assert result.content_type == "application/json"
    return CollectEndpointResponse.from_json(await result.text())


async def update_endpoint(client_session: ClientSession, endpoint: str, enabled: bool) -> None:
    result = await client_session.put(endpoint, json=ConfigureEndpointRequest(enabled=enabled).to_dict())
    assert result.status == HTTPStatus.NO_CONTENT


async def send_notification(client_session: ClientSession, webhook: str, method: str, body: str) -> int:
    result = await client_session.request(method, webhook, data=body)
    return result.status


async def delete_endpoint(client_session: ClientSession, endpoint: str) -> None:
    result = await client_session.delete(endpoint)
    assert result.status == HTTPStatus.NO_CONTENT


@pytest.mark.SERVER_URL("https://my.fake.website:1234/")
@pytest.mark.MOUNT_POINT("/my/api/")
async def test_create_and_manage_endpoints(client_session: ClientSession):
    """High level run through of some basic functionality"""

    # Create some endpoints
    endpoint1 = await create_endpoint(client_session, "/my/api/manage/endpoint")
    assert endpoint1.fully_qualified_endpoint.startswith("https://my.fake.website:1234/my/api/")

    endpoint2 = await create_endpoint(client_session, "/my/api/manage/endpoint")
    assert endpoint1.endpoint_id != endpoint2.endpoint_id

    endpoint3 = await create_endpoint(client_session, "/my/api/manage/endpoint")

    # Test that all of our collections return empty
    endpoint1_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint1.endpoint_id}")
    endpoint2_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}")
    endpoint3_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint3.endpoint_id}")
    assert len(endpoint1_notifications.notifications) == 0
    assert len(endpoint2_notifications.notifications) == 0
    assert len(endpoint3_notifications.notifications) == 0

    # Send notifications to endpoint 1
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint1.endpoint_id}", "PUT", "req2")) == 200
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint1.endpoint_id}", "GET", "")) == 200

    # Send notification to endpoint 2 - then disable / re-enable it
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint2.endpoint_id}", "POST", "req4")) == 200
    await update_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}", enabled=False)
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint2.endpoint_id}", "POST", "req5")) == 500
    await update_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}", enabled=True)
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint2.endpoint_id}", "POST", "req6")) == 200

    # A bad endpoint_id should 404
    assert (await send_notification(client_session, "/my/api/webhook/thisdne", "POST", "req7")) == 404

    # Collect notifications
    endpoint3_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint3.endpoint_id}")
    endpoint2_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}")
    endpoint1_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint1.endpoint_id}")
    assert len(endpoint1_notifications.notifications) == 3
    assert len(endpoint2_notifications.notifications) == 2, "One notification was dropped due to being disabled"
    assert len(endpoint3_notifications.notifications) == 0
    assert [(n.method, n.body) for n in endpoint1_notifications.notifications] == [
        ("POST", "req1"),
        ("PUT", "req2"),
        ("GET", ""),
    ]
    assert [(n.method, n.body) for n in endpoint2_notifications.notifications] == [
        ("POST", "req4"),
        ("POST", "req6"),
    ]

    # Test that all of our collections return empty afterwards (collection should've consumed things)
    endpoint1_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint1.endpoint_id}")
    endpoint2_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}")
    endpoint3_notifications = await collect_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint3.endpoint_id}")
    assert len(endpoint1_notifications.notifications) == 0
    assert len(endpoint2_notifications.notifications) == 0
    assert len(endpoint3_notifications.notifications) == 0

    # Test we can delete an empty and NON empty endpoint
    assert (await send_notification(client_session, f"/my/api/webhook/{endpoint1.endpoint_id}", "POST", "req7")) == 200
    await delete_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint1.endpoint_id}")
    await delete_endpoint(client_session, f"/my/api/manage/endpoint/{endpoint2.endpoint_id}")
    assert (await client_session.get(f"/my/api/manage/endpoint/{endpoint1.endpoint_id}")).status == 404
    assert (await client_session.get(f"/my/api/manage/endpoint/{endpoint2.endpoint_id}")).status == 404
    assert (await client_session.get(f"/my/api/manage/endpoint/{endpoint3.endpoint_id}")).status == 200


@pytest.mark.MAX_ACTIVE_ENDPOINTS("3")
@pytest.mark.MAX_ENDPOINT_NOTIFICATIONS("2")
async def test_max_endpoint_limits(client_session: ClientSession):
    """Do we get errors if we try and create too many things"""

    # Create some endpoints
    endpoint1 = await create_endpoint(client_session, "/manage/endpoint")
    endpoint2 = await create_endpoint(client_session, "/manage/endpoint")
    await create_endpoint(client_session, "/manage/endpoint")

    # Can't create more unless we delete first
    result = await client_session.post("/manage/endpoint")
    assert result.status == HTTPStatus.INSUFFICIENT_STORAGE
    await delete_endpoint(client_session, f"/manage/endpoint/{endpoint2.endpoint_id}")
    await create_endpoint(client_session, "/manage/endpoint")

    # Send notifications till full
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req2")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req3")) == 507

    # Collect and send more
    endpoint1_notifications = await collect_endpoint(client_session, f"/manage/endpoint/{endpoint1.endpoint_id}")
    assert [(n.method, n.body) for n in endpoint1_notifications.notifications] == [
        ("POST", "req1"),
        ("POST", "req2"),
    ]
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req4")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req5")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req6")) == 507
    endpoint1_notifications = await collect_endpoint(client_session, f"/manage/endpoint/{endpoint1.endpoint_id}")
    assert [(n.method, n.body) for n in endpoint1_notifications.notifications] == [
        ("POST", "req4"),
        ("POST", "req5"),
    ]


@pytest.mark.MAX_IDLE_DURATION_SECONDS("2")
@pytest.mark.MAX_DURATION_SECONDS("5")
@pytest.mark.CLEANUP_FREQUENCY_SECONDS("0.05")
async def test_endpoint_cleanup(client_session: ClientSession):
    """Is the cleanup task firing and working as expected"""

    endpoint1 = await create_endpoint(client_session, "/manage/endpoint")
    endpoint2 = await create_endpoint(client_session, "/manage/endpoint")
    endpoint3 = await create_endpoint(client_session, "/manage/endpoint")

    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint2.endpoint_id}", "POST", "req2")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint3.endpoint_id}", "POST", "req3")) == 200

    await asyncio.sleep(1)

    # Touch endpoint 1/2 but not 3
    await collect_endpoint(client_session, f"/manage/endpoint/{endpoint1.endpoint_id}")
    await collect_endpoint(client_session, f"/manage/endpoint/{endpoint2.endpoint_id}")

    await asyncio.sleep(1.1)

    # Touch endpoint 1/2 (they should be live) but endpoint 3 should be cleaned up now
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint2.endpoint_id}", "POST", "req2")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint3.endpoint_id}", "POST", "req3")) == 404, (
        "This should've expired by now"
    )

    await asyncio.sleep(1)

    # Touch endpoint 1 but not 2
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200

    await asyncio.sleep(1.1)

    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200
    assert (await send_notification(client_session, f"/webhook/{endpoint2.endpoint_id}", "POST", "req2")) == 404, (
        "This should've expired by now"
    )
    assert (await send_notification(client_session, f"/webhook/{endpoint3.endpoint_id}", "POST", "req3")) == 404, (
        "This should've expired by now"
    )

    # Max wall time has expired for endpoint 1
    await asyncio.sleep(1.1)

    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 404, (
        "This should've expired by now"
    )
    assert (await send_notification(client_session, f"/webhook/{endpoint2.endpoint_id}", "POST", "req2")) == 404, (
        "This should've expired by now"
    )
    assert (await send_notification(client_session, f"/webhook/{endpoint3.endpoint_id}", "POST", "req3")) == 404, (
        "This should've expired by now"
    )


async def test_server_info(client_session: ClientSession):

    # Empty server info
    result = await client_session.get("/manage")
    assert result.status == HTTPStatus.OK
    assert result.content_type == "text/plain"
    text_before = await result.text()
    assert text_before

    # Create some endpoints / notifications
    endpoint1 = await create_endpoint(client_session, "/manage/endpoint")
    await create_endpoint(client_session, "/manage/endpoint")
    assert (await send_notification(client_session, f"/webhook/{endpoint1.endpoint_id}", "POST", "req1")) == 200

    # Full server info
    result = await client_session.get("/manage")
    assert result.status == HTTPStatus.OK
    assert result.content_type == "text/plain"
    text_after = await result.text()
    assert text_after
    assert text_after != text_before
