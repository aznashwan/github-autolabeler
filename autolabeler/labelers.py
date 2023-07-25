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
import logging
from typing import Self

from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from selectors import Selector


LOG = logging.getLogger()


class BaseLabeler(metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def get_labels_for_repo(self, repo: Repository) -> list[Label]:
        raise NotImplemented

    @abc.abstractmethod
    def get_labels_for_pr(self, pr: PullRequest) -> list[Label]:
        raise NotImplemented

    @abc.abstractmethod
    def get_lebels_for_issue(self, issue: Issue) -> list[Label]:
        raise NotImplemented


class Labeler(BaseLabeler):
    def __init__(self,
                 label_name: str,
                 label_color: str,
                 selectors: list[Selector],
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

        missing = [field in val for field in required_fields]
        if missing:
            raise ValueError(
                f"Missing required fields {missing} in Labeler definition: {val}")

        sels_defs = val.get("selectors", [])
        sels = [Selector.from_dict(s) for s in sels_defs]

        return cls(label_name, val['label-color'], sels, prefix=prefix)




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

    def _prefix_labels(self, labels: list[Label]) -> list[Label]:
        for l in labels:
            l.name = f"{self._prefix}{self._separator}{l.name}"
        return labels
