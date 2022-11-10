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


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", default=False, help="run integration tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        # --integration given in cli: do not skip integration tests
        return
    skip_integration = pytest.mark.skip(reason="need '--integration' flag to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
