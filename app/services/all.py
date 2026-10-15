import asyncio


async def safe_call(coro, timeout=12.0):
    """Llama un scraper pero lo corta si tarda más de X segundos."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return []  # si falla o se pasa de tiempo, devolvemos lista vacía
