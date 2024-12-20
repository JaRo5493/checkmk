#!/usr/bin/env python3
# Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import logging
import multiprocessing
import signal
from pathlib import Path
from threading import Event
from typing import Callable, Self

from pydantic import BaseModel

from cmk.utils.hostaddress import HostName

from cmk.messaging import Channel, CMKConnectionError, DeliveryTag, RoutingKey
from cmk.piggyback import (
    PiggybackMessage,
    store_piggyback_raw_data,
    watch_new_messages,
)

from .config import load_config, PiggybackHubConfig
from .paths import create_paths
from .utils import make_log_and_exit, reconnect


class PiggybackPayload(BaseModel):
    source_host: str
    target_host: str
    last_update: int
    last_contact: int | None
    sections: bytes

    @classmethod
    def from_message(cls, message: PiggybackMessage) -> Self:
        return cls(
            source_host=message.meta.source,
            target_host=message.meta.piggybacked,
            last_update=message.meta.last_update,
            last_contact=message.meta.last_contact,
            sections=message.raw_data,
        )


def save_payload_on_message(
    logger: logging.Logger,
    omd_root: Path,
) -> Callable[[Channel[PiggybackPayload], DeliveryTag, PiggybackPayload], None]:
    def _on_message(
        channel: Channel[PiggybackPayload], delivery_tag: DeliveryTag, received: PiggybackPayload
    ) -> None:
        logger.debug(
            "Received payload for piggybacked host '%s' from source host '%s'",
            received.target_host,
            received.source_host,
        )
        store_piggyback_raw_data(
            source_hostname=HostName(received.source_host),
            piggybacked_raw_data={HostName(received.target_host): (received.sections,)},
            timestamp=received.last_update,
            omd_root=omd_root,
            status_file_timestamp=received.last_contact,
        )
        channel.acknowledge(delivery_tag)

    return _on_message


class SendingPayloadProcess(multiprocessing.Process):
    def __init__(self, logger: logging.Logger, omd_root: Path, reload_config: Event) -> None:
        super().__init__()
        self.logger = logger
        self.omd_root = omd_root
        self.site = omd_root.name
        self.paths = create_paths(omd_root)
        self.reload_config = reload_config
        self.task_name = "publishing on queue 'payload'"

    def run(self):
        self.logger.info("Starting: %s", self.task_name)
        signal.signal(
            signal.SIGTERM,
            make_log_and_exit(self.logger.debug, f"Stopping: {self.task_name}"),
        )

        config = load_config(self.paths)
        self.logger.debug("Loaded configuration: %r", config)

        try:
            for conn in reconnect(self.omd_root, self.logger, self.task_name):
                with conn:
                    channel = conn.channel(PiggybackPayload)
                    for piggyback_message in watch_new_messages(self.omd_root):
                        self._handle_message(channel, config, piggyback_message)

        except CMKConnectionError as exc:
            self.logger.error("Stopping: %s: %s", self.task_name, exc)
        except Exception as exc:
            self.logger.exception("Exception: %s: %s", self.task_name, exc)
            raise

    def _handle_message(
        self,
        channel: Channel[PiggybackPayload],
        config: PiggybackHubConfig,
        message: PiggybackMessage,
    ) -> None:
        config = self._check_for_config_reload(config)

        if (site_id := config.targets.get(message.meta.piggybacked, self.site)) is self.site:
            return

        self.logger.debug(
            "%s: from host '%s' to host '%s' on site '%s'",
            self.task_name.title(),
            message.meta.source,
            message.meta.piggybacked,
            site_id,
        )
        channel.publish_for_site(
            site_id, PiggybackPayload.from_message(message), routing=RoutingKey("payload")
        )

    def _check_for_config_reload(self, current_config: PiggybackHubConfig) -> PiggybackHubConfig:
        if not self.reload_config.is_set():
            return current_config
        self.logger.debug("Reloading configuration")
        config = load_config(self.paths)
        self.reload_config.clear()
        return config
