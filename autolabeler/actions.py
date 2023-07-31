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
from typing import Self, Union

from github.Issue import Issue
from github.PullRequest import PullRequest

from autolabeler import utils


LOG = utils.getStdoutLogger(__name__)

ActionableObject = Union[PullRequest, Issue]


class BasePostLabellingAction(metaclass=abc.ABCMeta):

    @abc.abstractclassmethod
    def from_dict(cls, val: dict) -> Self:
        _ = val
        return NotImplemented

    @abc.abstractmethod
    def run_post_action_for_matches(self, obj: ActionableObject, matches: list[dict]):
        _ = matches
        return NotImplemented


class OnMatchFormatAction(BasePostLabellingAction):
    """ Runs a simple action if able to format at least one selector match. """

    def __init__(
            self, if_format: str, perform_action_format: str,
            comment_format: str|None=None):
        self._if_format = if_format
        self._action_format = perform_action_format
        self._with_comment_format = comment_format

    @classmethod
    def from_dict(cls, val: dict) -> Self:
        supported_keys = ["if", "perform", "comment"]
        unsupported = [k for k in val if k not in supported_keys]
        if unsupported:
            raise ValueError(
                f"{cls}.from_dict() got unsupported keys: {unsupported}. "
                f"Supported keys are: {supported_keys}")

        missing = [k for k in supported_keys if k not in val]
        if missing:
            raise ValueError(
                f"{cls}.from_dict() missing required keys: {missing}. "
                f"Required keys are: {supported_keys}")

        return cls(val["if"], val["perform"], val["comment"])

    def _add_comment(self, obj: ActionableObject, comment: str):
        if isinstance(obj, PullRequest):
            obj = obj.as_issue()
        if comment:
            obj.create_comment(comment)

    def _change_state(self, obj: ActionableObject, state: str):
        if obj.state != state:
            obj.edit(state="state")
            obj.update()
        LOG.info(f"{obj} is now {state}")

    def run_post_action_for_matches(self, obj: ActionableObject, matches: list[dict]):
        triggers = []
        for match in matches:
            try:
                on = self._if_format.format(**match)
                action = self._action_format.format(**match)
                comment = None
                if self._with_comment_format:
                    comment = self._with_comment_format.format(**match)
                triggers.append((on, action, comment))
            except Exception as ex:
                msg = (
                    f"Failed to format action params '{self._if_format=}', "
                    f"'{self._action_format=}', or '{self._with_comment_format=}'. "
                    f"Selector match value were: {match}: {ex}")
                LOG.error(msg)
                continue
        LOG.info(
            f"{self}: trigger matches for object {obj} were: {triggers}")

        if triggers:
            actions = {t[1]: t for t in triggers}
            if len(actions) > 1:
                raise ValueError(
                    f"{self}: multiple actions ({actions}) triggered by matches: "
                    f"{matches}")
            action = list(actions.keys())[0]
            comments = {t[2] for t in  triggers}

            for comment in comments:
                self._add_comment(obj, comment)

            state = ""
            match action:
                case "close":
                    state = "closed"
                case "open":
                    state = "open"
                case other:
                    raise ValueError(
                        f"{self}: unsupported action '{other}")
            self._change_state(obj, state)
