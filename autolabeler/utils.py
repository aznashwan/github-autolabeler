# Copyright 2023 Cloudbase Solutions Srl
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging


class ColorFormatter(logging.Formatter):

    bold = "\x1b[1m"
    reset = "\x1b[0m"
    red = "\x1b[31;20m"
    cyan = "\x1b[36;20m"
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    datefmt = "%(asctime)s.%(msecs)s"
    format = f"{cyan}%(name)s:%(lineno)s{reset}: %(message)s"  # pyright: ignore

    FORMATS = {
        logging.DEBUG: f"{bold}{grey}{datefmt} %(levelname)s{reset} - {format}",
        logging.INFO: f"{bold}{grey}{datefmt} %(levelname)s{reset} - {format}",
        logging.WARNING: f"{bold}{yellow}{datefmt} %(levelname)s{reset} - {format}",
        logging.ERROR: f"{bold}{red}{datefmt} %(levelname)s{reset} - {format}",
        logging.CRITICAL: f"{bold}{red}{datefmt} %(levelname)s{reset} - {format}",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def setupLogging(level=logging.DEBUG):
    logger = logging.getLogger()
    logger.setLevel(level)

    # create console handler and set level to debug
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    formatter = ColorFormatter()
    ch.setFormatter(formatter)

    logger.addHandler(ch)

    return logger
