"""Fair Gunicorn `gthread` worker preventing single-worker connection hoarding (`API-01C` / `P4` `G18`).

Upstream `gunicorn.workers.gthread.ThreadWorker` defaults `--worker-connections=1000` and
accepts incoming connections from the shared kernel `listen` socket whenever
`self.nr_conns < self.worker_connections`. Under a `c=40` burst across `2` workers x `4` threads
behind Nginx (`Connection: close`), one worker process can greedily `accept()` 34-36 sockets into
its private `ThreadPoolExecutor` queue while the sibling worker process receives only 4-6 sockets,
causing bimodal latencies (`~95 ms` vs `~808-890 ms`).

`FairGthreadWorker` gates listener `accept()` on `len(self.futures) < max_in_flight` (defaulting
to `cfg.threads`), leaving excess concurrent connections in the shared Linux kernel `listen` queue
so whichever worker frees a thread first immediately accepts the next FIFO connection.
"""

from __future__ import annotations

import faulthandler
import functools
import os
import selectors
import time
from concurrent import futures

from gunicorn.workers.gthread import ThreadWorker


class FairGthreadWorker(ThreadWorker):
	"""`ThreadWorker` subclass bounding per-worker in-flight requests to thread capacity."""

	def _register_listeners(self) -> None:
		if getattr(self, "_listeners_registered", False):
			return
		for sock, acceptor in self._listener_acceptors:
			self.poller.register(sock, selectors.EVENT_READ, acceptor)
		self._listeners_registered = True

	def _unregister_listeners(self) -> None:
		if not getattr(self, "_listeners_registered", False):
			return
		for sock, _ in self._listener_acceptors:
			try:
				self.poller.unregister(sock)
			except (KeyError, ValueError, OSError):
				pass
		self._listeners_registered = False

	def accept(self, server, listener) -> None:
		if len(self.futures) >= self._max_in_flight:
			self._unregister_listeners()
			return
		super().accept(server, listener)
		if len(self.futures) >= self._max_in_flight:
			self._unregister_listeners()

	def run(self) -> None:
		self._max_in_flight = max(1, int(os.environ.get("CSKH_GUNICORN_MAX_INFLIGHT") or self.cfg.threads))
		self._listener_acceptors = []
		for sock in self.sockets:
			sock.setblocking(False)
			server = sock.getsockname()
			acceptor = functools.partial(self.accept, server)
			self._listener_acceptors.append((sock, acceptor))

		self._listeners_registered = False
		self._register_listeners()
		self.poller.register(self.shutdown_event, selectors.EVENT_READ, self.on_shutdown_event)

		while self.alive:
			self.notify()

			if len(self.futures) < self._max_in_flight and self.nr_conns < self.worker_connections:
				self._register_listeners()
				select_timeout = self.timeout or 1.0
				if self._keep:
					select_timeout = min(select_timeout, self.cfg.keepalive)
				events = self.poller.select(select_timeout)
				for key, _ in events:
					callback = key.data
					callback(key.fileobj)
				result = futures.wait(self.futures, timeout=0, return_when=futures.FIRST_COMPLETED)
			elif self._keep:
				self._unregister_listeners()
				events = self.poller.select(0.01)
				for key, _ in events:
					callback = key.data
					callback(key.fileobj)
				result = futures.wait(self.futures, timeout=0.01, return_when=futures.FIRST_COMPLETED)
			else:
				self._unregister_listeners()
				result = futures.wait(self.futures, timeout=0.2, return_when=futures.FIRST_COMPLETED)

			for fut in result.done:
				self.futures.remove(fut)

			if not self.is_parent_alive():
				break

			self.murder_keepalived()

			current_time = time.monotonic()
			for fut in self.futures:
				if current_time > fut._request_timeout:
					self.alive = False
					self.log.error("A request timed out. Exiting.")
					faulthandler.dump_traceback()

		self.tpool.shutdown(False)
		self.poller.close()

		for s in self.sockets:
			s.close()

		futures.wait(self.futures, timeout=self.cfg.graceful_timeout)
