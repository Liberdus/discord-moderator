"""Independent Discord service with explicit files, credentials, locks, and signals."""

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import signal
import time

from .discord_service import DiscordService, verify_discord_runtime
from .instance import credential_provider, load_policy
from .secure_files import checked_directory, token_lock, write_private


class SafeLogFilter(logging.Filter):
    EVENTS = frozenset(("starting", "connected", "disconnected", "stopped", "startup_failed",
                        "runtime_failed", "background_failure", "shutdown_requested"))

    def filter(self, record):
        # Third-party transport logs can contain interaction URLs, tokens or
        # response bodies. Accept only our closed set of operational events.
        return (record.name == "liberdus_moderator.service" and isinstance(record.msg, str) and record.msg in self.EVENTS
                and not record.args and not record.exc_info and not record.stack_info)


def configure_logging():
    handler = logging.StreamHandler()
    handler.addFilter(SafeLogFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Installed SDKs may already have installed their own stream handlers.
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()
            logger.propagate = True
    logging.captureWarnings(True)


class StandaloneService(DiscordService):
    def __init__(self, home):
        self.home = checked_directory(home)
        self.initialize_service()
        self.credentials = None
        self.token_context = None
        self.owns_database_lock = False
        self.finished = asyncio.Event()
        self.shutdown_lock = asyncio.Lock()
        self.error_code = None
        self.logger = logging.getLogger("liberdus_moderator.service")

    def load_startup(self):
        verify_discord_runtime()
        policy = load_policy(self.home)
        self.credentials = credential_provider(self.home)
        token = self.credentials.get("discord")
        if policy.ai_enabled:
            self.credentials.get("jev")
        return policy, Path(policy.storage.database_path), token

    def current_policy(self):
        return load_policy(self.home)

    def jev_key(self):
        return self.credentials.get("jev")

    def _acquire_platform_lock(self, scope, token, description):
        self.owns_database_lock = True
        context = token_lock(token)
        context.__enter__()
        self.token_context = context
        return True

    def _release_platform_lock(self):
        if self.token_context is not None:
            self.token_context.__exit__(None, None, None)
            self.token_context = None

    def write_status(self, state):
        record = {"state": state, "pid": os.getpid(), "updated_at": time.time(),
                  "connected": self.online, "error": self.error_code}
        write_private(self.home / "state/runtime.json", json.dumps(record) + "\n", replace=True)

    def _mark_connected(self):
        self.write_status("connected")
        self.logger.info("connected")

    def _mark_disconnected(self):
        # A service which failed to obtain a lock must not overwrite the active
        # instance's status during startup cleanup.
        if self.owns_database_lock:
            self.write_status("failed" if self.error_code else "stopped" if self.closing else "disconnected")
        self.logger.info("disconnected")

    def _set_fatal_error(self, code, message, *, retryable=False):
        # Deliberately do not retain the original exception or provider text.
        self.error_code = "startup_failed" if code == "liberdus_startup_failed" else "runtime_failed"
        self.logger.error(self.error_code)
        self.finished.set()

    async def disconnect(self):
        async with self.shutdown_lock:
            if self.owns_database_lock:
                self.online = False
                with suppress(OSError, ValueError):
                    self.write_status("failed" if self.error_code else "stopped")
            await super().disconnect()
            self.owns_database_lock = False


async def serve(home):
    service = StandaloneService(home)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    installed = []
    previous_handler = loop.get_exception_handler()

    def background_failure(loop, context):
        logging.getLogger("liberdus_moderator.service").error("background_failure")
        service.error_code = "runtime_failed"
        stopped.set()

    loop.set_exception_handler(background_failure)
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
        installed.append(signum)
    waiters = []
    try:
        service.logger.info("starting")
        startup = asyncio.create_task(service.connect())
        stop_waiter = asyncio.create_task(stopped.wait())
        waiters = [startup, stop_waiter]
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if stopped.is_set():
            return 0
        if not startup.result():
            return 2
        failure_waiter = asyncio.create_task(service.finished.wait())
        waiters.append(failure_waiter)
        await asyncio.wait((stop_waiter, failure_waiter), return_when=asyncio.FIRST_COMPLETED)
        service.logger.info("shutdown_requested")
        return 2 if service.error_code else 0
    finally:
        for task in waiters:
            task.cancel()
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        await service.disconnect()
        for signum in installed:
            loop.remove_signal_handler(signum)
        loop.set_exception_handler(previous_handler)
        service.logger.info("stopped")
