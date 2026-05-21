import re
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from cactus_schema.notification import CollectedNotification
from freezegun import freeze_time

from cactus_client_notifications.server.endpoint_store import (
    EndpointMetadata,
    EndpointStore,
    NotificationException,
    generate_unique_id,
)
from cactus_client_notifications.server.time import utc_now


def test_generate_unique_id():
    ids = []
    for _ in range(100):
        new_id = generate_unique_id()
        assert new_id and isinstance(new_id, str)
        assert re.search(r"[^A-Za-z\-_0-9]", new_id) is None, "Should only have alphanumeric chars"
        ids.append(new_id)

    assert len(ids) == len(set(ids)), "Should all be unique"


async def test_EndpointStore_empty_operations():
    """Sanity checks the basic operations of the EndpointStore work with an empty store"""
    store = EndpointStore(max_active_endpoints=3, max_endpoint_notifications=2)

    # test endpoints on empty store
    with pytest.raises(NotificationException) as exc_match:
        await store.collect_notifications("abc")
    assert exc_match.value.status_code == HTTPStatus.NOT_FOUND
    assert await store.try_delete_endpoint("abc") is False
    await store.cleanup_expired_endpoints(utc_now(), timedelta(seconds=1), timedelta(seconds=2))
    with pytest.raises(NotificationException) as exc_match:
        await store.add_notification("abc", generate_class_instance(CollectedNotification, generate_relationships=True))
    assert exc_match.value.status_code == HTTPStatus.NOT_FOUND
    with pytest.raises(NotificationException) as exc_match:
        await store.update_endpoint("abc", True)
    assert exc_match.value.status_code == HTTPStatus.NOT_FOUND


async def test_EndpointStore_basic_operations():
    """Sanity checks the basic operations of the EndpointStore"""
    store = EndpointStore(max_active_endpoints=4, max_endpoint_notifications=3)

    # test endpoints on empty store
    id1 = await store.create_endpoint()
    id2 = await store.create_endpoint()
    id3 = await store.create_endpoint()
    _ = await store.create_endpoint()
    with pytest.raises(NotificationException) as exc_match:
        await store.create_endpoint()  # Too many endpoints
    assert exc_match.value.status_code == HTTPStatus.INSUFFICIENT_STORAGE

    n1 = generate_class_instance(CollectedNotification, seed=1, generate_relationships=True)
    n2 = generate_class_instance(CollectedNotification, seed=2, generate_relationships=True)
    n3 = generate_class_instance(CollectedNotification, seed=3, generate_relationships=True)
    n4 = generate_class_instance(CollectedNotification, seed=4, generate_relationships=True)

    await store.add_notification(id1, n1)
    await store.add_notification(id1, n2)
    await store.add_notification(id1, n3)
    with pytest.raises(NotificationException) as exc_match:
        await store.add_notification(id1, n4)
    assert exc_match.value.status_code == HTTPStatus.INSUFFICIENT_STORAGE

    n5 = generate_class_instance(CollectedNotification, seed=5, generate_relationships=True)
    n6 = generate_class_instance(CollectedNotification, seed=6, generate_relationships=True)
    await store.add_notification(id2, n5)
    await store.add_notification(id2, n6)

    # Check the metadata
    metadata = await store.get_endpoint_metadata()
    assert_list_type(EndpointMetadata, metadata, count=4)

    assert await store.collect_notifications(id1) == [n1, n2, n3]
    assert await store.collect_notifications(id1) == []
    assert await store.collect_notifications(id2) == [n5, n6]
    assert await store.collect_notifications(id2) == []
    assert await store.collect_notifications(id3) == []

    n7 = generate_class_instance(CollectedNotification, seed=7, generate_relationships=True)
    n8 = generate_class_instance(CollectedNotification, seed=8, generate_relationships=True)

    await store.add_notification(id2, n7)
    await store.add_notification(id2, n8)
    assert await store.collect_notifications(id2) == [n7, n8]

    n9 = generate_class_instance(CollectedNotification, seed=9, generate_relationships=True)
    await store.add_notification(id1, n9)
    assert await store.try_delete_endpoint(id1) is True
    assert await store.try_delete_endpoint(id1) is False
    assert await store.try_delete_endpoint(id2) is True

    n10 = generate_class_instance(CollectedNotification, seed=10, generate_relationships=True)
    with pytest.raises(NotificationException) as exc_match:
        await store.add_notification(id1, n10)
    assert exc_match.value.status_code == HTTPStatus.NOT_FOUND
    n10 = generate_class_instance(CollectedNotification, seed=10, generate_relationships=True)
    with pytest.raises(NotificationException) as exc_match:
        await store.add_notification(id2, n10)
    assert exc_match.value.status_code == HTTPStatus.NOT_FOUND

    await store.add_notification(id3, n10)


async def test_EndpointStore_cleanup():
    """Tests that the cleanup operation correctly wipes out expired endpoints"""
    store = EndpointStore(max_active_endpoints=99, max_endpoint_notifications=99)

    now = datetime(2017, 3, 4, 5, 6, 7, tzinfo=UTC)
    max_idle = timedelta(seconds=60)
    max_duration = timedelta(seconds=180)

    # expired_duration has a recent request but has met total expiry time
    with freeze_time(now - timedelta(seconds=200)):
        expired_duration = await store.create_endpoint()
    with freeze_time(now - timedelta(seconds=5)):
        await store.add_notification(expired_duration, generate_class_instance(CollectedNotification, seed=1))

    # expired idle is within duration but has idled out
    with freeze_time(now - timedelta(seconds=100)):
        expired_idle = await store.create_endpoint()
    with freeze_time(now - timedelta(seconds=90)):
        await store.add_notification(expired_idle, generate_class_instance(CollectedNotification, seed=2))

    # not_expired is totally fine
    with freeze_time(now - timedelta(seconds=20)):
        not_expired = await store.create_endpoint()
        await store.add_notification(not_expired, generate_class_instance(CollectedNotification, seed=3))

    # future_time is totally fine
    with freeze_time(now + timedelta(seconds=20)):
        future_time = await store.create_endpoint()
        await store.add_notification(future_time, generate_class_instance(CollectedNotification, seed=4))

    # Do the cleanup
    await store.cleanup_expired_endpoints(now, max_idle=max_idle, max_duration=max_duration)

    # The try_delete will tell us what cleaned up
    assert await store.try_delete_endpoint(expired_duration) is False, "Should've deleted"
    assert await store.try_delete_endpoint(expired_idle) is False, "Should've deleted"
    assert await store.try_delete_endpoint(not_expired) is True, "Should've been left alone"
    assert await store.try_delete_endpoint(future_time) is True, "Should've been left alone"


async def test_EndpointStore_disabled_endpoint():
    """Tests that the cleanup operation correctly wipes out expired endpoints"""

    store = EndpointStore(max_active_endpoints=99, max_endpoint_notifications=99)

    n1 = generate_class_instance(CollectedNotification, seed=1)
    n2 = generate_class_instance(CollectedNotification, seed=2)
    n3 = generate_class_instance(CollectedNotification, seed=3)
    n4 = generate_class_instance(CollectedNotification, seed=4)

    id1 = await store.create_endpoint()
    id2 = await store.create_endpoint()
    await store.add_notification(id1, n1)
    await store.update_endpoint(id1, enabled=False)
    with pytest.raises(NotificationException) as exc_match:
        await store.add_notification(id1, n2)  # ID1 is now disabled
    assert exc_match.value.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    await store.add_notification(id2, n3)
    await store.update_endpoint(id1, enabled=True)
    await store.add_notification(id1, n4)

    assert await store.collect_notifications(id1) == [n1, n4]
    assert await store.collect_notifications(id2) == [n3]
