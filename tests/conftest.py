"""Set up some common test helper things."""
import asyncio
import datetime
import functools
import logging
import ssl
import threading
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request
import multidict
import pytest
import requests_mock as _requests_mock

from homeassistant import core as ha, loader, runner, util
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY
from homeassistant.auth.models import Credentials
from homeassistant.auth.providers import homeassistant
from homeassistant.components.websocket_api.auth import (
    TYPE_AUTH,
    TYPE_AUTH_OK,
    TYPE_AUTH_REQUIRED,
)
from homeassistant.components.websocket_api.http import URL
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers import config_entry_oauth2_flow, event
from homeassistant.setup import async_setup_component
from homeassistant.util import location

pytest.register_assert_rewrite("tests.common")

from tests.common import (  # noqa: E402, isort:skip
    CLIENT_ID,
    INSTANCES,
    MockUser,
    async_test_home_assistant,
    mock_storage as mock_storage,
)


logging.basicConfig(level=logging.DEBUG)
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

asyncio.set_event_loop_policy(runner.HassEventLoopPolicy(False))
# Disable fixtures overriding our beautiful policy
asyncio.set_event_loop_policy = lambda policy: None


def pytest_configure(config):
    """Register marker for tests that log exceptions."""
    config.addinivalue_line(
        "markers", "no_fail_on_log_exception: mark test to not fail on logged exception"
    )


def check_real(func):
    """Force a function to require a keyword _test_real to be passed in."""

    @functools.wraps(func)
    async def guard_func(*args, **kwargs):
        real = kwargs.pop("_test_real", None)

        if not real:
            raise Exception(
                'Forgot to mock or pass "_test_real=True" to %s', func.__name__
            )

        return await func(*args, **kwargs)

    return guard_func


# Guard a few functions that would make network connections
location.async_detect_location_info = check_real(location.async_detect_location_info)
util.get_local_ip = lambda: "127.0.0.1"


@pytest.fixture(autouse=True)
def verify_cleanup():
    """Verify that the test has cleaned up resources correctly."""
    threads_before = frozenset(threading.enumerate())

    yield

    if len(INSTANCES) >= 2:
        count = len(INSTANCES)
        for inst in INSTANCES:
            inst.stop()
        pytest.exit(f"Detected non stopped instances ({count}), aborting test run")

    threads = frozenset(threading.enumerate()) - threads_before
    assert not threads


@pytest.fixture(autouse=True)
def bcrypt_cost():
    """Run with reduced rounds during tests, to speed up uses."""
    import bcrypt

    gensalt_orig = bcrypt.gensalt

    def gensalt_mock(rounds=12, prefix=b"2b"):
        return gensalt_orig(4, prefix)

    bcrypt.gensalt = gensalt_mock
    yield
    bcrypt.gensalt = gensalt_orig


@pytest.fixture
def hass_storage():
    """Fixture to mock storage."""
    with mock_storage() as stored_data:
        yield stored_data


@pytest.fixture
def load_registries():
    """Fixture to control the loading of registries when setting up the hass fixture.

    To avoid loading the registries, tests can be marked with:
    @pytest.mark.parametrize("load_registries", [False])
    """
    return True


@pytest.fixture
def hass(loop, load_registries, hass_storage, request):
    """Fixture to provide a test instance of Home Assistant."""

    def exc_handle(loop, context):
        """Handle exceptions by rethrowing them, which will fail the test."""
        # Most of these contexts will contain an exception, but not all.
        # The docs note the key as "optional"
        # See https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.call_exception_handler
        if "exception" in context:
            exceptions.append(context["exception"])
        else:
            exceptions.append(
                Exception(
                    "Received exception handler without exception, but with message: %s"
                    % context["message"]
                )
            )
        orig_exception_handler(loop, context)

    exceptions = []
    hass = loop.run_until_complete(async_test_home_assistant(loop, load_registries))
    orig_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(exc_handle)

    yield hass

    loop.run_until_complete(hass.async_stop(force=True))
    for ex in exceptions:
        if isinstance(ex, ServiceNotFound):
            continue
        raise ex


@pytest.fixture
async def stop_hass():
    """Make sure all hass are stopped."""
    orig_hass = ha.HomeAssistant

    created = []

    def mock_hass():
        hass_inst = orig_hass()
        created.append(hass_inst)
        return hass_inst

    with patch("homeassistant.core.HomeAssistant", mock_hass):
        yield

    for hass_inst in created:
        if hass_inst.state == ha.CoreState.stopped:
            continue

        with patch.object(hass_inst.loop, "stop"):
            await hass_inst.async_block_till_done()
            await hass_inst.async_stop(force=True)


@pytest.fixture
def requests_mock():
    """Fixture to provide a requests mocker."""
    with _requests_mock.mock() as m:
        yield m


@pytest.fixture
def mock_device_tracker_conf():
    """Prevent device tracker from reading/writing data."""
    devices = []

    async def mock_update_config(path, id, entity):
        devices.append(entity)

    with patch(
        "homeassistant.components.device_tracker.legacy"
        ".DeviceTracker.async_update_config",
        side_effect=mock_update_config,
    ), patch(
        "homeassistant.components.device_tracker.legacy.async_load_config",
        side_effect=lambda *args: devices,
    ):
        yield devices


@pytest.fixture
async def hass_admin_credential(hass, local_auth):
    """Provide credentials for admin user."""
    return Credentials(
        id="mock-credential-id",
        auth_provider_type="homeassistant",
        auth_provider_id=None,
        data={"username": "admin"},
        is_new=False,
    )


@pytest.fixture
async def hass_access_token(hass, hass_admin_user, hass_admin_credential):
    """Return an access token to access Home Assistant."""
    await hass.auth.async_link_user(hass_admin_user, hass_admin_credential)

    refresh_token = await hass.auth.async_create_refresh_token(
        hass_admin_user, CLIENT_ID, credential=hass_admin_credential
    )
    return hass.auth.async_create_access_token(refresh_token)


@pytest.fixture
def hass_owner_user(hass, local_auth):
    """Return a Home Assistant admin user."""
    return MockUser(is_owner=True).add_to_hass(hass)


@pytest.fixture
def hass_admin_user(hass, local_auth):
    """Return a Home Assistant admin user."""
    admin_group = hass.loop.run_until_complete(
        hass.auth.async_get_group(GROUP_ID_ADMIN)
    )
    return MockUser(groups=[admin_group]).add_to_hass(hass)


@pytest.fixture
def hass_read_only_user(hass, local_auth):
    """Return a Home Assistant read only user."""
    read_only_group = hass.loop.run_until_complete(
        hass.auth.async_get_group(GROUP_ID_READ_ONLY)
    )
    return MockUser(groups=[read_only_group]).add_to_hass(hass)


@pytest.fixture
def hass_read_only_access_token(hass, hass_read_only_user, local_auth):
    """Return a Home Assistant read only user."""
    credential = Credentials(
        id="mock-readonly-credential-id",
        auth_provider_type="homeassistant",
        auth_provider_id=None,
        data={"username": "readonly"},
        is_new=False,
    )
    hass_read_only_user.credentials.append(credential)

    refresh_token = hass.loop.run_until_complete(
        hass.auth.async_create_refresh_token(
            hass_read_only_user, CLIENT_ID, credential=credential
        )
    )
    return hass.auth.async_create_access_token(refresh_token)


@pytest.fixture
def local_auth(hass):
    """Load local auth provider."""
    prv = homeassistant.HassAuthProvider(
        hass, hass.auth._store, {"type": "homeassistant"}
    )
    hass.loop.run_until_complete(prv.async_initialize())
    hass.auth._providers[(prv.type, prv.id)] = prv
    return prv


@pytest.fixture
def current_request():
    """Mock current request."""
    with patch("homeassistant.components.http.current_request") as mock_request_context:
        mocked_request = make_mocked_request(
            "GET",
            "/some/request",
            headers={"Host": "example.com"},
            sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS),
        )
        mock_request_context.get.return_value = mocked_request
        yield mock_request_context


@pytest.fixture
def current_request_with_host(current_request):
    """Mock current request with a host header."""
    new_headers = multidict.CIMultiDict(current_request.get.return_value.headers)
    new_headers[config_entry_oauth2_flow.HEADER_FRONTEND_BASE] = "https://example.com"
    current_request.get.return_value = current_request.get.return_value.clone(
        headers=new_headers
    )


@pytest.fixture
def hass_ws_client(aiohttp_client, hass_access_token, hass):
    """Websocket client fixture connected to websocket server."""

    async def create_client(hass=hass, access_token=hass_access_token):
        """Create a websocket client."""
        assert await async_setup_component(hass, "websocket_api", {})

        client = await aiohttp_client(hass.http.app)

        with patch("homeassistant.components.http.auth.setup_auth"):
            websocket = await client.ws_connect(URL)
            auth_resp = await websocket.receive_json()
            assert auth_resp["type"] == TYPE_AUTH_REQUIRED

            if access_token is None:
                await websocket.send_json(
                    {"type": TYPE_AUTH, "access_token": "incorrect"}
                )
            else:
                await websocket.send_json(
                    {"type": TYPE_AUTH, "access_token": access_token}
                )

            auth_ok = await websocket.receive_json()
            assert auth_ok["type"] == TYPE_AUTH_OK

        # wrap in client
        websocket.client = client
        return websocket

    return create_client


@pytest.fixture(autouse=True)
def fail_on_log_exception(request, monkeypatch):
    """Fixture to fail if a callback wrapped by catch_log_exception or coroutine wrapped by async_create_catching_coro throws."""
    if "no_fail_on_log_exception" in request.keywords:
        return

    def log_exception(format_err, *args):
        raise

    monkeypatch.setattr("homeassistant.util.logging.log_exception", log_exception)


@pytest.fixture
def mock_zeroconf():
    """Mock zeroconf."""
    with patch("homeassistant.components.zeroconf.HaZeroconf") as mock_zc:
        yield mock_zc.return_value


@pytest.fixture
def enable_custom_integrations(hass):
    """Enable custom integrations defined in the test dir."""
    hass.data.pop(loader.DATA_CUSTOM_COMPONENTS)
