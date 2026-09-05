"""Background worker service entrypoint with graceful shutdown."""

import asyncio
import signal
import sys
from datetime import datetime, timezone
from packages.domain.config import get_settings
from packages.domain.logging import logger, setup_logging

settings = get_settings()
setup_logging(settings.LOG_LEVEL)


class BackgroundWorker:
    """Resilient background worker for event processing and tasks."""

    def __init__(self):
        self.is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the background worker processing loop."""
        self.is_running = True
        logger.info(
            "Background worker started successfully",
            extra={"environment": settings.ENVIRONMENT, "started_at": datetime.now(timezone.utc).isoformat()},
        )

        try:
            while not self._shutdown_event.is_set():
                await self.process_cycle()
                # Sleep between cycles, wake immediately on shutdown
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("Worker execution task cancelled")
        finally:
            await self.cleanup()

    async def process_cycle(self):
        """Execute one processing cycle (e.g. check queues, background jobs)."""
        # In Phase 1.3, this will poll queued events from storage repository
        pass

    async def cleanup(self):
        """Perform resource cleanup during graceful shutdown."""
        logger.info("Cleaning up background worker resources...")
        self.is_running = False
        logger.info("Background worker shutdown complete")

    def stop(self):
        """Signal worker to stop gracefully."""
        logger.info("Shutdown signal received, initiating graceful worker shutdown...")
        self._shutdown_event.set()


async def main():
    worker = BackgroundWorker()

    loop = asyncio.get_running_loop()

    # Handle termination signals
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
    else:
        # Windows compatibility
        signal.signal(signal.SIGINT, lambda s, f: worker.stop())
        signal.signal(signal.SIGTERM, lambda s, f: worker.stop())

    await worker.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user interrupt")
