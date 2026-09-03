from pathlib import Path

import pytest

from tests.tier_policy import classify_test_tier

TEST_ROOT = Path(__file__).parent.resolve()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.path)).resolve()
        try:
            relative_path = path.relative_to(TEST_ROOT)
        except ValueError:
            continue
        item.add_marker(classify_test_tier(relative_path))
