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
from typing import Self

from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import actions
from autolabeler import selectors
from autolabeler import utils


LOG = utils.getStdoutLogger(__name__)


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
    def run_post_labelling_actions(self, obj: utils.LabellableObject):
        _ = obj
        return NotImplemented


class SelectorLabeler(BaseLabeler):
    def __init__(self,
                 label_name: str,
                 label_color: str,
                 label_description: str,
                 selectors: list[selectors.Selector],
                 actioner: actions.BasePostLabellingAction|None=None):
        self._name = label_name
        self._color = label_color
        self._description = label_description
        self._actioner = actioner
        self._selectors = selectors

    @classmethod
    def from_dict(cls, label_name: str, val: dict) -> Self:
        required_fields = [
            "label-color", "label-description"]
        if not type(val) is dict:
            raise TypeError(
                f"Expected dict with keys {required_fields}, "
                f"got {val} ({type(val)})")

        missing = [field for field in required_fields if field not in val]
        if missing:
            raise ValueError(
                f"Missing required fields {missing} in Labeler definition: {val}")

        sels = []
        sels_defs = val.get("selectors", {})
        for sname, sbody in sels_defs.items():
            try:
                # HACK(aznashwan): disable raising for testing.
                scls = selectors.get_selector_cls(sname, raise_if_missing=False)
                if scls:
                    sels.append(scls.from_dict(sbody))
            except Exception as ex:
                raise ValueError(
                    f"Failed to load selector '{sname}' from "
                    f"payload {sbody}:\n{ex}") from ex

        actioner = None
        actioner_def = val.get("action", {})
        if actioner_def:
            actioner = actions.OnMatchFormatAction.from_dict(actioner_def)

        return cls(
            label_name,
            val['label-color'], val['label-description'], sels, actioner=actioner)

    def _run_selectors(self, obj: Issue|PullRequest|Repository) -> list[list[dict]]:
        # overly-drawn-out code for logging purposes:
        selector_matches = []
        for selector in self._selectors:
            res = selector.match(obj)
            selector_matches.append(res)
            LOG.debug(f"{selector}.match({obj}) = {res}")
        return selector_matches

    def _get_labels_for_selector_matches(
            self, selector_matches: list[list[dict]]) -> list[LabelParams]:
        if not self._selectors:
            # This is a static label and should be returned.
            return [LabelParams(
                self._name, self._color, self._description)]

        if self._selectors and not selector_matches:
            # NOTE(aznashwan): if no selector fired, no labels should be returned.
            LOG.debug(f"{self} matched no selector")
            return []

        label_defs = {}
        for match_set in selector_matches:
            for match in match_set:
                new = None
                try:
                    name = self._name.format(**match)
                    description = self._description.format(**match)
                    new = LabelParams(
                        name, self._color,
                        description)
                except Exception as ex:
                    msg = (
                        f"Failed to format match data into '{self._name}' and "
                        f"'{self._description}'. Selector match values were: "
                        f"{match}: {ex}")
                    LOG.error(msg)
                    continue

                if name not in label_defs:
                    label_defs[name] = new
                elif label_defs[name] != new:
                    LOG.warning(
                        f"{self} got conflicting colors/descriptions for label "
                        f"{name}: value already present ({label_defs[name]}) "
                        f"different from new value: {new}")
            LOG.debug(f"{self} determined following labels: {label_defs}")

        return list(label_defs.values())

    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        # If this a simple label with a static name, it always applies to the repo.
        try:
            self._name.format()
            self._description.format()
            return [LabelParams(self._name, self._color, self._description)]
        except KeyError:
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

    def run_post_labelling_actions(self, obj: utils.LabellableObject):
        if not self._actioner:
            return

        selector_matches = self._run_selectors(obj)
        self._actioner.run_post_action_for_matches(obj, selector_matches)


class PrefixLabeler(BaseLabeler):
    """ Class for handling label prefix groups.

    Simply aggregates/delegates labels generation to the contained concrete
    label generators and just adds its prefix to all labels.
    """

    def __init__(self, prefix: str, sublabelers: list[BaseLabeler],
                 separator='/'):
        if not sublabelers:
            raise ValueError(
                f"{self} expects at least one sub-labeler. Got: {sublabelers}")
        self._prefix = prefix
        self._separator = separator
        self._sublabelers = sublabelers

    def _prefix_label(self, label: LabelParams) -> LabelParams:
        label.name = f"{self._prefix}{self._separator}{label.name}"
        return label

    def _prefix_labels(self, labels: list[LabelParams]) -> list[LabelParams]:
        return list(map(self._prefix_label, labels))

    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        res = [slblr.get_labels_for_repo(repo) for slblr in self._sublabelers]
        return self._prefix_labels(list(itertools.chain(*res)))

    def get_labels_for_pr(self, pr: PullRequest) -> list[LabelParams]:
        res = [slblr.get_labels_for_pr(pr) for slblr in self._sublabelers]
        return self._prefix_labels(list(itertools.chain(*res)))

    def get_labels_for_issue(self, issue: Issue) -> list[LabelParams]:
        res = [slblr.get_labels_for_issue(issue) for slblr in self._sublabelers]
        return self._prefix_labels(list(itertools.chain(*res)))

    def run_post_labelling_actions(self, obj: utils.LabellableObject):
        for sublabeler in self._sublabelers:
            sublabeler.run_post_labelling_actions(obj)


def load_labelers_from_config(config: dict) -> list[BaseLabeler]:
    toplevel_labelers = []
    for key, val in config.items():
        LOG.info(f"Attempting to load labeler from: {config}")
        # Assume it's a plain labeler until proven otherwise.
        try:
            toplevel_labelers.append(SelectorLabeler.from_dict(key, val))
            continue
        except (ValueError, TypeError) as err:
            LOG.info(
                f"Failed to load labeler on key {key}, assuming it's a prefix: {err}")

        prefixer = PrefixLabeler(key, load_labelers_from_config(val))
        toplevel_labelers.append(prefixer)

    return toplevel_labelers
