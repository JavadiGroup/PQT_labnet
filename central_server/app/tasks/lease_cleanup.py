import asyncio


async def lease_cleanup_loop(
    lease_manager,
    interval_seconds: int = 5,
) -> None:
    """Periodically remove expired leases and restore device status."""

    while True:
        try:
            lease_manager.cleanup_expired_leases()

        except Exception as exc:
            lease_manager.storage.append_log({
                "event": "BACKGROUND_TASK_ERROR",
                "metadata": {
                    "task": "lease_cleanup_loop",
                    "error": str(exc),
                },
            })

        await asyncio.sleep(interval_seconds)