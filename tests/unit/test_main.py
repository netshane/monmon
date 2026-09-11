import pytest
from unittest.mock import MagicMock, patch
import sys
from locallib.dependencies import get_settings

"""
Test: main

This is a sample test file. You can use this as a template for writing your own tests.
"""

# # All tests in this module are skipped by default until this is removed
# pytestmark = pytest.mark.skip(
#     reason="This should contain the reason to skip all tests."
# )


# run this with the -s flag to see loguru output in the console during testing
def console_logging():
    from loguru import logger

    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> :: <level>{message}</level>",
        level="INFO",
    )


@pytest.fixture()
def sample_mock():
    ctx = MagicMock()
    ctx.obj = {}
    return ctx


@pytest.fixture()
def sample_mock_path_exists():
    with patch("os.path.exists", return_value=True) as mock_exists:
        with patch(
            "os.stat",
            return_value=MagicMock(st_size=1024, st_mtime=1234567890),
        ) as mock_stat:
            yield mock_exists, mock_stat


@pytest.mark.unit
def test_sample(sample_mock):
    get_settings()

    assert sample_mock.obj == {}
