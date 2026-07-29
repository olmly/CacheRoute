"""Background ZMQ subscriber for LMCache/vLLM cache visibility events."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    import zmq
    import zmq.asyncio
except ImportError:  # pragma: no cover - runtime dependency is optional during static analysis
    zmq = None  # type: ignore

logger = logging.getLogger("proxy.cache.zmq_subscriber")


class ZMQCacheSubscriber:
    """负责 ZMQ 生命周期、重连和安全关闭；不直接理解消息语义。"""

    def __init__(
        self,
        on_message: Callable[[Any], Awaitable[None]],
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._on_message = on_message
        self._logger = logger_ or logger
        self._endpoint = os.environ.get("PROXY_CACHE_ZMQ_ENDPOINT", "").strip()
        self._topic = os.environ.get("PROXY_CACHE_ZMQ_TOPIC", "").encode("utf-8")
        self._reconnect_delay_s = max(1.0, float(os.environ.get("PROXY_CACHE_ZMQ_RECONNECT_DELAY_S", "3") or 3.0))
        self._poll_timeout_ms = max(100, int(float(os.environ.get("PROXY_CACHE_ZMQ_POLL_TIMEOUT_MS", "1000") or 1000)))
        self._state: Dict[str, Any] = {
            "enabled": bool(self._endpoint),
            "endpoint": self._endpoint or None,
            "topic": self._topic.decode("utf-8", errors="ignore"),
            "connected": False,
            "connect_count": 0,
            "message_count": 0,
            "error_count": 0,
            "last_message_at": None,
            "last_error_at": None,
            "last_error": None,
            "first_message_logged": False,
        }

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint)

    def snapshot_state(self) -> Dict[str, Any]:
        return dict(self._state)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        if not self._endpoint:
            self._logger.info("[Proxy][CacheZMQ] subscriber disabled because PROXY_CACHE_ZMQ_ENDPOINT is empty")
            return
        if zmq is None:
            self._record_error("import", "pyzmq_not_installed")
            self._logger.warning("[Proxy][CacheZMQ] pyzmq is unavailable, subscriber disabled")
            return

        while not stop_event.is_set():
            context = None
            socket = None
            try:
                context = zmq.asyncio.Context.instance()
                socket = context.socket(zmq.SUB)
                socket.setsockopt(zmq.SUBSCRIBE, self._topic)
                socket.setsockopt(zmq.LINGER, 0)
                socket.connect(self._endpoint)
                self._state["connected"] = True
                self._state["connect_count"] = int(self._state.get("connect_count", 0) or 0) + 1
                self._logger.info("[Proxy][CacheZMQ] connected endpoint=%s topic=%s", self._endpoint, self._state["topic"])

                while not stop_event.is_set():
                    try:
                        events = await socket.poll(self._poll_timeout_ms)
                    except Exception as exc:
                        self._record_error("poll", f"{type(exc).__name__}:{exc}")
                        break
                    if not events:
                        continue

                    try:
                        frames = await socket.recv_multipart()
                    except Exception as exc:
                        self._record_error("recv", f"{type(exc).__name__}:{exc}")
                        break

                    payload = frames[-1] if frames else b""
                    self._state["message_count"] = int(self._state.get("message_count", 0) or 0) + 1
                    self._state["last_message_at"] = int(time.time() * 1000)
                    if not self._state.get("first_message_logged"):
                        self._state["first_message_logged"] = True
                        self._logger.info("[Proxy][CacheZMQ] first raw event received endpoint=%s", self._endpoint)
                    await self._on_message(payload)

            except Exception as exc:
                self._record_error("connect", f"{type(exc).__name__}:{exc}")
            finally:
                self._state["connected"] = False
                if socket is not None:
                    try:
                        socket.close(0)
                    except Exception:
                        pass

            if not stop_event.is_set():
                self._logger.warning(
                    "[Proxy][CacheZMQ] reconnect scheduled endpoint=%s delay_s=%s last_error=%s",
                    self._endpoint,
                    self._reconnect_delay_s,
                    self._state.get("last_error"),
                )
                await stop_event.wait(self._reconnect_delay_s)

    def _record_error(self, stage: str, detail: str) -> None:
        self._state["error_count"] = int(self._state.get("error_count", 0) or 0) + 1
        self._state["last_error_at"] = int(time.time() * 1000)
        self._state["last_error"] = f"{stage}:{detail}"
