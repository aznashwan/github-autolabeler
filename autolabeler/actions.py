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

import abc
import enum
import logging
from typing import Self, Union

from github.Issue import Issue
from github.PullRequest import PullRequest

from autolabeler import expr
from autolabeler.selectors import MatchResult


LOG = logging.getLogger(__name__)

ActionableObject = Union[PullRequest, Issue]


class PostLabellingAction(enum.Enum):
    OPEN = 'open'
    CLOSE = 'close'
    APPROVE = 'approve'


class BasePostLabellingAction(metaclass=abc.ABCMeta):

    @abc.abstractclassmethod
    def from_dict(cls, val: dict) -> Self:
        _ = val
        return NotImplemented

    @abc.abstractmethod
    def get_post_labelling_action(self, match: MatchResult) -> PostLabellingAction:
        return NotImplemented

    @abc.abstractmethod
    def get_post_labelling_comment(self, match: MatchResult) -> str:
        return NotImplemented


class OnMatchFormatAction(BasePostLabellingAction):
    """ Returns a simple action and formatted comment based on the given match. """

    def __init__(
            self, perform_action_format: str|None=None,
            comment_format: str|None=None):
        self._action_format = perform_action_format or ""
        self._comment_format = comment_format or ""

    def __repr__(self):
        cls = self.__class__.__name__
        perform = self._action_format
        comment = self._comment_format[:16]
        return f"{cls}({perform=}, {comment=})"

    @classmethod
    def from_dict(cls, val: dict) -> Self:
        if not isinstance(val, dict):
            raise TypeError(f"{cls.__name__}.from_dict() got non-dict: {val}")

        supported_keys = ["perform", "comment"]
        unsupported = [k for k in val if k not in supported_keys]
        if unsupported:
            raise ValueError(
                f"{cls}.from_dict() got unsupported keys: {unsupported}. "
                f"Supported keys are: {supported_keys}")

        if not val:
            raise ValueError(
                f"{cls}.from_dict() missing at least one supported key: {supported_keys}.")

        supported_actions = ["open", "close"]
        action = val.get("perform", None)
        if action and action not in supported_actions:
            raise ValueError(
                f"{cls}.from_dict() got unsupported 'perform' action: {action}. "
                f"Supported actions are: {supported_actions}")

        return cls(perform_action_format=action, comment_format=val["comment"])

    def get_post_labelling_action(self, match: MatchResult) -> PostLabellingAction|None:
        if not self._action_format:
            return None
        return PostLabellingAction(
            expr.format_string_with_expressions(
                self._action_format, match))

    def get_post_labelling_comment(self, match: MatchResult) -> str|None:
        if not self._comment_format:
            return None
        return expr.format_string_with_expressions(
            self._comment_format, match)
