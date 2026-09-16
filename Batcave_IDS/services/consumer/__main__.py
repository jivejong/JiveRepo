"""Entry point for the consumer service (`python -m services.consumer`).

Runs as a docker compose service under `make dev-up` (docs/05), which is why
`./data` is mounted into the container — landed Parquet has to appear on the
host where DuckDB and dbt read it, without a copy step. Also runnable directly
against a local broker for iteration.

Config follows the house convention in services/honeypot/app.py: environment
variables with working defaults, so nothing needs a .env to run locally.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from confluent_kafka import Consumer

from services.consumer.consumer import LandingConsumer

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "attack.events")
CONSUMER_GROUP = os.environ.get("CONSUMER_GROUP", "attack-events-writer")
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data"))
FLUSH_MAX_MESSAGES = int(os.environ.get("CONSUMER_FLUSH_MAX_MESSAGES", "5000"))
FLUSH_INTERVAL_S = float(os.environ.get("CONSUMER_FLUSH_INTERVAL_S", "30"))


def build_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
            # The entire point of this service. Auto-commit would advance
            # offsets on a timer regardless of whether the batch reached disk,
            # which is the silent-data-loss failure this phase exists to avoid.
            "enable.auto.commit": False,
        }
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    consumer = build_consumer()
    consumer.subscribe([KAFKA_TOPIC])

    landing = LandingConsumer(
        consumer,
        data_root=DATA_ROOT,
        flush_max_messages=FLUSH_MAX_MESSAGES,
        flush_interval_s=FLUSH_INTERVAL_S,
    )
    landing.install_signal_handlers()

    logging.getLogger("consumer").info(
        "consuming %s from %s as group %s; landing under %s (flush: %d messages / %.0fs)",
        KAFKA_TOPIC,
        KAFKA_BOOTSTRAP_SERVERS,
        CONSUMER_GROUP,
        DATA_ROOT.resolve(),
        FLUSH_MAX_MESSAGES,
        FLUSH_INTERVAL_S,
    )
    landing.run()


if __name__ == "__main__":
    main()
