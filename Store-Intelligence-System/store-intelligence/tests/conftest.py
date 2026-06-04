"""pytest configuration for async tests."""
import pytest
import pytest_asyncio

# Configure pytest-asyncio
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )
