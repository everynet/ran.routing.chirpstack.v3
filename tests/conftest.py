import pytest
import structlog


@pytest.fixture()
def log_capture():
    """
    usage:
    def tested_function():
        logger.info("Test")
        return 42

    def test_x(log_capture):
        res = tested_function()
        assert res == 42
        assert log_capture.entries[0]["event"] == "Test"

    :return: LogCapture instance with stored logs in "entries" attribute
    :rtype: LogCapture
    """
    return structlog.testing.LogCapture()


@pytest.fixture(autouse=True)
def fixture_configure_structlog(log_capture):
    structlog.configure(processors=[log_capture])


def pytest_configure(config):
    """
    Allows plugins and conftest files to perform initial configuration.
    This hook is called for every plugin and initial conftest
    file after command line options have been parsed.
    """
    if config.pluginmanager.getplugin("asyncio"):
        config.option.asyncio_mode = "auto"
    config.addinivalue_line("markers", "integration: mark test as integration to run")
    config.addinivalue_line("markers", "upstream: mark test as integration/upstream.")
    config.addinivalue_line("markers", "downstream: mark test as integration/downstream.")
    config.addinivalue_line("markers", "multicast: mark test as integration/multicast.")


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", default=False, help="Run integration tests")
    parser.addoption("--no-upstream", action="store_true", default=False, help="Disable upstream tests")
    parser.addoption("--no-downstream", action="store_true", default=False, help="Disable downstream tests")
    parser.addoption("--no-multicast", action="store_true", default=False, help="Disable multicast tests")
    # Configure region for integration tests
    parser.addoption("--region", action="store", default="eu868", help="Region, used for testing")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        # --integration given in cli: do not skip integration tests

        for skip_tests_part in ("upstream", "downstream", "multicast"):
            if config.getoption(f"--no-{skip_tests_part}"):
                skip_flag = pytest.mark.skip(reason=f"Skipped because of '--no-{skip_tests_part}' flag")
                for item in items:
                    if skip_tests_part in item.keywords:
                        item.add_marker(skip_flag)

        return

    skip_integration = pytest.mark.skip(reason="need '--integration' flag to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
