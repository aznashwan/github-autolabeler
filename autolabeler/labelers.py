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
import dataclasses
import itertools
import logging
import re
import traceback
from typing import Self

from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import actions, utils
from autolabeler import expr
from autolabeler import selectors


LOG = logging.getLogger(__name__)


@dataclasses.dataclass
class LabelParams:
    name: str
    color: str
    description: str

    def __init__(self, name: str, color: str, description: str):
        self.name = name
        self.color = color
        self.description = description.strip()

    @classmethod
    def from_label(cls, label: Label) -> Self:
        return cls(label.name, label.color, str(label.description))

    @classmethod
    def from_dict(cls, val: dict[str, str]) -> Self:
        return cls(val.get("name", ""),
                   val.get("color", ""),
                   val.get("description", ""))

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "color": self.color,
            "description": self.description}

    def to_label_creation_params(self) -> dict[str, str]:
        return self.to_dict()

    def __eq__(self, other: Self) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self.name == other.name and \
               self.color == other.color and \
               self.description == other.description


class BaseLabeler(metaclass=abc.ABCMeta):
    """ ABC offering independent labelling behavior for each Github resource type.
    """

    @abc.abstractmethod
    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        _ = repo
        return NotImplemented

    @abc.abstractmethod
    def get_labels_for_pr(self, pr: PullRequest) -> list[LabelParams]:
        _ = pr
        return NotImplemented

    @abc.abstractmethod
    def get_labels_for_issue(self, issue: Issue) -> list[LabelParams]:
        _ = issue
        return NotImplemented

    @abc.abstractmethod
    def run_post_labelling_actions(self, obj: Issue|PullRequest):
        _ = obj
        return NotImplemented


class SelectorLabeler(BaseLabeler):
    def __init__(self,
                 label_name: str,
                 label_color: str,
                 label_description: str,
                 selectors: list[selectors.Selector]|None=None,
                 condition: str|None=None,
                 actioner: actions.BasePostLabellingAction|None=None):
        self._name = label_name
        self._color = utils.map_color_string(label_color)
        self._description = label_description
        self._selectors = selectors or []
        self._condition = condition
        self._actioner = actioner

    def __repr__(self):
        cls = self.__class__.__name__
        name = self._name
        color = self._color
        desc = self._description[:16]
        condition = self._condition
        actioner = self._actioner
        return f"{cls}({name=}, {color=}, {desc=}, {condition=}, {actioner=})"

    @classmethod
    def from_dict(cls, label_name: str, val: dict) -> Self:
        required_fields = [
            "color", "description"]
        if not type(val) is dict:
            raise TypeError(
                f"Expected dict with keys {required_fields}, "
                f"got {val} ({type(val)})")

        missing = [field for field in required_fields if field not in val]
        if missing:
            raise ValueError(
                f"Missing required fields {missing} in SelectorLabeler definition: {val}")

        supported_fields = ["selectors", "action", "if"]
        supported_fields.extend(required_fields)
        unsupported = [f for f in val if f not in supported_fields]
        if unsupported:
            raise ValueError(
                f"Unsupported fields for SelectorLabeler for {label_name=}: "
                f"{unsupported}. Supported fields are: {supported_fields}")

        sels = []
        sels_defs = val.get("selectors", {})
        for sname, sbody in sels_defs.items():
            try:
                scls = selectors.get_selector_cls(sname, raise_if_missing=True)
                sels.append(scls.from_val(sbody))
            except Exception as ex:
                raise ValueError(
                    f"Failed to load selector '{sname}' from "
                    f"payload {sbody}:\n{ex}") from ex

        actioner = None
        actioner_def = val.get("action", {})
        if actioner_def:
            actioner = actions.OnMatchFormatAction.from_dict(actioner_def)

        return cls(
            label_name, val['color'], val['description'],
            selectors=sels, actioner=actioner, condition=val.get('if'))

    def _run_selectors(self, obj: Issue|PullRequest|Repository) -> dict:
        # overly-drawn-out code for logging purposes:
        matches = {}
        for selector in self._selectors:
            res = selector.match(obj)
            matches[selector.get_selector_name()] = res
            LOG.debug(f"{selector}.match({obj}) = {res}")
        return matches

    def _run_statement(self, statement: str, variables: dict) -> object:
        try:
            statement = statement.strip()
            expr.check_string_expression(statement, variables)
            return expr.evaluate_string_expression(statement, variables)
        except (NameError, SyntaxError) as ex:
            raise ex.__class__(
                f"Failed to run statement '{statement}': {ex}") from ex

    def _format_label_string(self, string: str, selector_matches: dict) -> str:
        label_format_group_re = r"\{([^\}]+)\}"
        regex = re.compile(label_format_group_re)
        result = ""
        search_pos = 0
        while True:
            match = regex.search(string, pos=search_pos)
            if not match:
                return f"{result}{string[search_pos:]}"
            result = f"{result}{string[search_pos:match.start()]}"

            statement = match.group(0)
            expr_res = self._run_statement(
                statement[1:-1], selector_matches)
            result = f"{result}{expr_res}"
            search_pos = match.end()

    def _get_labels_for_selector_matches(
            self,
            selector_matches: dict[str, list[selectors.MatchResult]]
        ) -> list[LabelParams]:
        if not self._selectors:
            # This is a static label and should be returned.
            return [LabelParams(
                self._name, self._color, self._description)]

        successful_matches = []
        selector_matches_index_map = {}  # maps index in `selector_matches` to its name
        for selector, match in selector_matches.items():
            if match:
                selector_matches_index_map[len(successful_matches)] = selector
                successful_matches.append(match)
        if self._selectors and not successful_matches:
            # NOTE(aznashwan): if no selector matched at all, return:
            LOG.debug(f"{self} had no selector matches whatsoever, returning.")
            return []

        new_labels_map = {}
        for match_set in itertools.product(*successful_matches):
            # The match_dict will map selector names to their results.
            match_dict = {
                selector_matches_index_map[i]: selector_match
                for i, selector_match in enumerate(match_set)}

            new = None
            LOG.debug(
                f"{self}._get_labels_for_selector_matches(): attempting to format "
                f"with selectors match values: {match_dict}")
            try:
                name = self._format_label_string(
                    self._name, match_dict)
                description = self._format_label_string(
                    self._description, match_dict)
                if self._condition:
                    condition_result = self._run_statement(
                        self._condition, match_dict)
                    if not bool(condition_result):
                        LOG.debug(
                            f"{self}: conditional check for '{self._condition}' "
                            f"failed with {condition_result} for match set: "
                            f"{match_set}")
                        continue
                new = LabelParams(name, self._color, description)
            except NameError as e:
                LOG.error(
                    f"{self}: skipping error formatting label params {e}"
                    f"{traceback.format_exc()}")
            if not new:
                continue

            if new.name in new_labels_map and new_labels_map[new.name] != new:
                LOG.warning(
                    f"{self} got conflicting colors/descriptions for label "
                    f"{new.name}: value already present {new_labels_map.get(new.name)}"
                    f" is different from new value: {new}")

            new_labels_map[new.name] = new

        return list(new_labels_map.values())

    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        # If this a simple label with a static name, it always applies to the repo.
        try:
            name = self._format_label_string(self._name, {})
            desc = self._format_label_string(self._description, {})
            return [LabelParams(name, self._color, desc)]
        except Exception as ex:
            LOG.debug(
                f"{self}.get_labels_for_repo({repo}): failed to format "
                "label name/description. Running selectors.")
            # Else, we must run and generate the selectors:
            return self._get_labels_for_selector_matches(
                self._run_selectors(repo))

    def _get_nonstatic_labels(self, obj: PullRequest|Issue):
        # TODO(aznashwan): separate `StaticLabeler` class.
        # NOTE(aznashwan): this prevents static labellers with no selectors
        # being from applied to all PRs/Issues.
        if not self._selectors:
            return []

        return self._get_labels_for_selector_matches(
            self._run_selectors(obj))

    def get_labels_for_pr(self, pr: PullRequest) -> list[LabelParams]:
        return self._get_nonstatic_labels(pr)

    def get_labels_for_issue(self, issue: Issue) -> list[LabelParams]:
        return self._get_nonstatic_labels(issue)

    def run_post_labelling_actions(self, obj: Issue|PullRequest):
        if not self._actioner:
            return

        selector_matches = self._run_selectors(obj)
        self._actioner.run_post_action_for_matches(obj, selector_matches)


def load_labelers_from_config(
        config: dict, prefix: str="", separator: str="/") -> list[BaseLabeler]:
    if not isinstance(config, dict):
        raise ValueError(
            "Failed to recursively parse config: got to the following "
            "non-mapping object in hopes it would be a labeler definition "
            f"containing a 'color' and 'description' field: {config}")

    required_labeler_keys = ['color', 'description']
    labelers = []
    for key, val in config.items():
        name = key
        if prefix:
            name = f"{prefix}{separator}{key}"
        if all(k in val for k in required_labeler_keys):
            labelers.append(
                SelectorLabeler.from_dict(name, val))
        else:
            labelers.extend(load_labelers_from_config(
                val, prefix=name, separator=separator))

    return labelers
