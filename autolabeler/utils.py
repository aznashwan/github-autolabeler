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
import re


LOG = logging.getLogger(__name__)

RGB_COLOR_REGEX = re.compile("[a-fA-F0-9]{6}")

LABEL_COLOR_CODES = {
    "red": "B60205",
    "orange": "D93F0B",
    "yellow": "FBCA04",
    "green": "0E8A16",
    "teal": "006B75",
    "blue": "1D76DB",
    "navy": "0052CC",
    "bluer": "0052CC",
    "purple": "5319E7",
}


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


def map_color_string(color: str):
    color = LABEL_COLOR_CODES.get(color, color)
    if not RGB_COLOR_REGEX.match(color):
        raise ValueError(
            f"Invalid color format '{color}'. Color must be either a "
            f"6-hex-digit case-insensitive RGB value or one of the "
            f"following: {LABEL_COLOR_CODES}")
    return color.lower()


def merge_dicts(dict1: dict, dict2: dict) -> dict:
    """ Recursively merges the two given dicts,
    overwriting common keys from the first with the second.
    """
    if not isinstance(dict1, dict) or not isinstance(dict2, dict):
        raise TypeError(
            f"merge_dicts requires two dicts, got {dict1=} {dict2=}")

    keys1 = set(dict1.keys())
    keys2 = set(dict2.keys())

    res = {k: dict1[k] for k in (keys1 - keys2)}
    res.update({k: dict2[k] for k in (keys2 - keys1)})

    common = keys1.intersection(keys2)
    for key in common:
        v1 = dict1[key]
        v2 = dict2[key]

        if isinstance(v1, dict) and isinstance(v2, dict):
            res[key] = merge_dicts(v1, v2)
            continue

        if type(v1) != type(v2):
            LOG.warning(
                f"merge_dicts({dict1=}, {dict2=}): common key '{key}' "
                f"has different type in dict2 ({type(v1)=} != {type(v2)=})")
        res[key] = v2

    return res
