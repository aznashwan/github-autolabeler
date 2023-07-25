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
from typing import Self

from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import selectors
from autolabeler import utils


LOG = utils.getStdoutLogger(__name__)


class BaseLabeler(metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def get_labels_for_repo(self, repo: Repository) -> list[Label]:
        raise NotImplemented

    @abc.abstractmethod
    def get_labels_for_pr(self, pr: PullRequest) -> list[Label]:
        raise NotImplemented

    @abc.abstractmethod
    def get_labels_for_issue(self, issue: Issue) -> list[Label]:
        raise NotImplemented


class SelectorLabeler(BaseLabeler):
    def __init__(self,
                 label_name: str,
                 label_color: str,
                 selectors: list[selectors.Selector],
                 prefix: str=""):
        self._name = label_name
        self._color = label_color
        self._selectors = selectors
        self._prefix = prefix

    @classmethod
    def from_dict(cls, label_name: str, val: dict, prefix: str="") -> Self:
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

        return cls(label_name, val['label-color'], sels, prefix=prefix)

    def get_labels_for_repo(self, repo: Repository) -> list[Label]:
        raise NotImplemented

    def get_labels_for_pr(self, pr: PullRequest) -> list[Label]:
        raise NotImplemented

    def get_labels_for_issue(self, issue: Issue) -> list[Label]:
        raise NotImplemented


class PrefixLabeler(BaseLabeler):
    """ Class for handling label prefix groups.

    Simply aggregates/delegates labels generation to the contained concrete
    label generators and just adds its prefix to all labels.
    """

    def __init__(self, prefix: str, sublabelers: list[BaseLabeler],
                 separator='/'):
        self._prefix = prefix
        self._separator = separator
        self._sublabelers = sublabelers

    def get_labels_for_repo(self, repo: Repository) -> list[Label]:
        raise NotImplemented

    def get_labels_for_pr(self, pr: PullRequest) -> list[Label]:
        raise NotImplemented

    def get_labels_for_issue(self, issue: Issue) -> list[Label]:
        raise NotImplemented



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
