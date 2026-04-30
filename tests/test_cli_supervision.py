from __future__ import annotations

import asyncio

import pytest

from killtrader.cli import _run_supervised_tasks
from killtrader.core.errors import NoDataAvailableError


def _contains_no_data_error(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains_no_data_error(nested) for nested in exc.exceptions)
    return isinstance(exc, NoDataAvailableError)


def test_supervised_tasks_cancel_siblings_and_raise_terminal_error() -> None:
    asyncio.run(_assert_supervision_halts())


async def _assert_supervision_halts() -> None:
    worker_started = asyncio.Event()
    worker_cancelled = asyncio.Event()

    async def worker() -> None:
        worker_started.set()
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    async def source_failure() -> None:
        await worker_started.wait()
        raise NoDataAvailableError("all configured real market data sources failed; trading halted")

    with pytest.raises(ExceptionGroup) as exc_info:
        await _run_supervised_tasks(worker, source_failure)

    assert _contains_no_data_error(exc_info.value)
    assert worker_cancelled.is_set()
