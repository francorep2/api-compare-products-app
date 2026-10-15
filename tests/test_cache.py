import asyncio

import pytest

from app.providers import http


def test_concurrent_calls_share_one_computation():
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "ok"

    async def run():
        return await asyncio.gather(*[http.cached("k-coalesce", 10, factory) for _ in range(10)])

    assert asyncio.run(run()) == ["ok"] * 10
    assert calls == 1


def test_errors_are_not_cached():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "ok"

    async def run():
        with pytest.raises(RuntimeError):
            await http.cached("k-err", 10, flaky)
        return await http.cached("k-err", 10, flaky)

    assert asyncio.run(run()) == "ok"
