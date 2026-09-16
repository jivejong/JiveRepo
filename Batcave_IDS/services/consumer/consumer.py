"""The poll loop: buffer, flush, then commit — in that order.

**The ordering is the whole point of this module.** Offsets are committed only
after every Parquet file and quarantine file in the batch is safely on disk. A
crash in between replays the batch and produces duplicates, which is
at-least-once delivery working as designed (docs/01) — deduplication is
staging's job in Phase 5.

Inverting it (commit first, write after) produces a consumer that passes every
unit test here and silently loses a batch on every crash, which is why the
checkpoint verifies the ordering by killing the process mid-batch rather than by
reading this file. See docs/exercises.md.

SIGTERM (`docker stop`, `make dev-down`) is trapped and drains: normal shutdown
strands nothing. SIGKILL (`docker kill`) cannot be trapped, and that is exactly
what the restart exercise uses to force the replay.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from services.consumer.writer import (
    PartitionKey,
    parse_timestamp,
    partition_key,
    write_parquet,
    write_quarantine,
)

log = logging.getLogger("consumer")


class KafkaConsumer(Protocol):
    """The slice of confluent_kafka.Consumer this module uses, so the tests can
    drive the loop with a fake instead of a broker."""

    def poll(self, timeout: float) -> Any: ...
    def commit(self, asynchronous: bool = ...) -> Any: ...
    def close(self) -> None: ...


class LandingConsumer:
    def __init__(
        self,
        consumer: KafkaConsumer,
        data_root: Path,
        flush_max_messages: int = 5_000,
        flush_interval_s: float = 30.0,
        clock: Any = time.monotonic,
        debug_pre_commit_delay_s: float = 0.0,
    ) -> None:
        self.consumer = consumer
        self.data_root = data_root
        self.flush_max_messages = flush_max_messages
        self.flush_interval_s = flush_interval_s
        self.clock = clock
        # Testing aid ONLY, default 0 (no-op in normal operation). The real
        # write-then-commit window this class exists to get right is normally
        # microseconds wide - too narrow to land `docker kill` in on purpose.
        # Set CONSUMER_DEBUG_PRE_COMMIT_DELAY_S to widen it so the restart
        # exercise (docs/exercises.md) can hit it deterministically instead of
        # by timing luck.
        self.debug_pre_commit_delay_s = debug_pre_commit_delay_s

        self.buffer: dict[PartitionKey, list[dict[str, Any]]] = defaultdict(list)
        self.quarantined: list[tuple[int, int, bytes | None, bytes, str]] = []
        self.buffered = 0
        self.last_flush = clock()
        self.running = True

        # Counters, reported on shutdown and asserted by the tests.
        self.flushes = 0
        self.rows_landed = 0
        self.rows_quarantined = 0

    # -- shutdown ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)

    def _request_stop(self, signum, _frame) -> None:
        log.info("signal %s received; draining before exit", signum)
        self.running = False

    # -- the loop ---------------------------------------------------------

    def run(self) -> None:
        try:
            while self.running:
                message = self.consumer.poll(1.0)
                if message is not None:
                    self.handle(message)
                if self.should_flush():
                    self.flush_and_commit()
            # Graceful shutdown: land what's buffered rather than replaying it.
            self.flush_and_commit()
        finally:
            self.consumer.close()
            log.info(
                "stopped after %d flushes: %d rows landed, %d quarantined",
                self.flushes,
                self.rows_landed,
                self.rows_quarantined,
            )

    def handle(self, message: Any) -> None:
        if message.error() is not None:
            log.warning("kafka error, skipping: %s", message.error())
            return

        raw = message.value()
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError(f"expected a JSON object, got {type(event).__name__}")
        except (ValueError, UnicodeDecodeError) as exc:
            # The undeserializable pathology (docs/03 row 6). Park it and keep
            # going — the offset still commits with this batch, because
            # quarantining IS the successful handling of this message.
            self.quarantined.append(
                (message.partition(), message.offset(), message.key(), raw, str(exc))
            )
            self.buffered += 1
            return

        event["kafka_partition"] = message.partition()
        event["kafka_offset"] = message.offset()
        event["landed_at"] = datetime.now(UTC)

        self.buffer[self._partition_for(event)].append(event)
        self.buffered += 1

    def _partition_for(self, event: dict[str, Any]) -> PartitionKey:
        """Hive partition from `received_at`.

        If `received_at` is missing or unparseable, fall back to the consumer's
        own clock and land the row with its original bad value intact, so Phase
        5 staging can still flag it (docs/02). No current pathology produces
        this, but one null would otherwise crash the batch — the halt-on-bad-data
        failure this phase exists to prevent.
        """
        received_at = parse_timestamp(event.get("received_at"))
        if received_at is None:
            log.warning(
                "event %s has unusable received_at %r; partitioning by landed_at",
                event.get("event_id"),
                event.get("received_at"),
            )
            received_at = event["landed_at"]
        return partition_key(str(event.get("event_kind", "unknown")), received_at)

    def should_flush(self) -> bool:
        if self.buffered == 0:
            return False
        if self.buffered >= self.flush_max_messages:
            return True
        return (self.clock() - self.last_flush) >= self.flush_interval_s

    def flush_and_commit(self) -> None:
        """Write everything, then commit. Never the other way round.

        If a write raises, the commit does not happen and the batch is replayed
        on restart — the failure mode is duplicate data, never lost data.
        """
        if self.buffered == 0:
            return

        for key, rows in self.buffer.items():
            write_parquet(self.data_root, key, rows)
            self.rows_landed += len(rows)
        for partition, offset, key_bytes, raw, error in self.quarantined:
            write_quarantine(self.data_root, partition, offset, key_bytes, raw, error)
            self.rows_quarantined += 1

        if self.debug_pre_commit_delay_s:
            # Never reached in normal operation (see __init__). Holds the batch
            # open, written-but-uncommitted, so a kill here proves the ordering.
            log.warning(
                "DEBUG: holding %.1fs after flush, before commit - "
                "written but uncommitted; a kill now must replay this batch",
                self.debug_pre_commit_delay_s,
            )
            time.sleep(self.debug_pre_commit_delay_s)

        # Only now. Everything above is durable.
        self.consumer.commit(asynchronous=False)

        log.info(
            "flushed %d messages across %d partitions (%d quarantined); offsets committed",
            self.buffered,
            len(self.buffer),
            len(self.quarantined),
        )
        self.buffer.clear()
        self.quarantined.clear()
        self.buffered = 0
        self.last_flush = self.clock()
        self.flushes += 1
