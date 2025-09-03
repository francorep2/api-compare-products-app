# --- FIX Windows: usar ProactorEventLoop para permitir subprocess ---
import sys, asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
# -------------------------------------------------------------------