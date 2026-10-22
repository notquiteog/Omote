"""Keep a local validation job from consuming the desktop's remaining RAM."""
import pytest

from tools.run_memory_limited import GIB, memory_budget


def test_memory_budget_reserves_room_for_the_desktop_without_counting_swap():
    assert memory_budget(32 * GIB, 18 * GIB) <= 12 * GIB
    assert memory_budget(128 * GIB, 120 * GIB) <= 77 * GIB
    assert memory_budget(32 * GIB, 18 * GIB, 8) == 8 * GIB


@pytest.mark.parametrize('requested', [0, -1, 20, float('inf'), float('nan')])
def test_overlarge_or_invalid_budgets_cannot_start(requested):
    with pytest.raises((ValueError, OverflowError)):
        memory_budget(32 * GIB, 18 * GIB, requested)


def test_low_available_memory_refuses_even_on_a_large_machine():
    with pytest.raises(ValueError):
        memory_budget(128 * GIB, 4 * GIB)
