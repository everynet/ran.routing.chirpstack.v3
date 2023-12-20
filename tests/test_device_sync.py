from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.device_sync import DeviceSync


@pytest.fixture
def ran_core_mock():
    mock = AsyncMock()
    return mock


@pytest.fixture
def device_list_mock():
    mock = AsyncMock()
    return mock


@pytest.fixture(scope="function")
def device_sync(ran_core_mock, device_list_mock):
    return DeviceSync(ran=ran_core_mock, device_list=device_list_mock)


@pytest.mark.asyncio
async def test_device_exists_in_both_different_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = None

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16) + 1  # A different join_eui
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.delete.assert_called_once()
    device_sync.ran.routing_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_active_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_target_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.active_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_both_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16) + 1
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_active_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.delete.assert_called_once()
    device_sync.ran.routing_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_target_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.active_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.delete.assert_called_once()
    device_sync.ran.routing_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_different_both_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16) + 1
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16) + 1
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.delete.assert_called_once()
    device_sync.ran.routing_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_active_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_target_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.active_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_both_dev_addr_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16)
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_active_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_target_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.active_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_same_both_dev_addr_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = "a" * 8

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.target_dev_addr = int(cs_device.dev_addr, 16)
    ran_device.active_dev_addr = int(cs_device.dev_addr, 16)
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_dev_addr_none_with_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = "f" * 16
    cs_device.dev_addr = None

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = int(cs_device.join_eui, 16)
    ran_device.active_dev_addr = None
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_in_both_dev_addr_none_without_join_eui(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.join_eui = None
    cs_device.dev_addr = None

    ran_device = MagicMock()
    ran_device.dev_eui = int(cs_device.dev_eui, 16)
    ran_device.join_eui = None
    ran_device.active_dev_addr = None
    ran_device.target_dev_addr = None
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.update.assert_not_called()
    device_sync.ran.routing_table.delete.assert_not_called()
    device_sync.ran.routing_table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_device_exists_only_in_chirpstack(device_sync):
    # Setup
    cs_device = MagicMock()
    cs_device.dev_eui = "f" * 16
    cs_device.dev_addr = "a" * 8
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[cs_device])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_device_exists_only_in_ran(device_sync):
    # Setup
    ran_device = MagicMock()
    ran_device.dev_eui = int("f" * 16, 16)
    ran_device.active_dev_addr = int("a" * 8, 16)
    device_sync._fetch_chirpstack_devices = AsyncMock(return_value=[])
    device_sync._fetch_ran_devices = AsyncMock(return_value=[ran_device])

    # Act
    await device_sync.perform_full_sync()

    # Assert
    device_sync.ran.routing_table.delete.assert_called_once()
