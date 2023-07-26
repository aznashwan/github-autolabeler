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

from github import Github
from github.Label import Label

LOG = logging.getLogger()


class BaseLabelsTarget(metaclass=abc.ABCMeta):
    """ ABC for GitHub API entities which can have labels applies to them. """

    # @abc.abstractclassmethod
    # def from_str(cls, client: Github, target_str: str) -> Self:
    #     LOG.debug(f"BaseLabelsTarget.from_str({client}, {target_str})")
    #     raise NotImplemented

    @abc.abstractmethod
    def get_labels(self) -> list[Label]:
        raise NotImplemented

    @abc.abstractmethod
    def set_labels(self, labels: list[Label]):
        _ = labels
        raise NotImplemented

    @abc.abstractmethod
    def remove_labels(self, labels: list[str]):
        _ = labels
        raise NotImplemented


class RepoLabelsTarget(BaseLabelsTarget):

    def __init__(self, client: Github, repo_user: str, repo_name: str):
        self._client = client
        self._repo_user = repo_user
        self._repo_name = repo_name
        self._repo_handle = client.get_repo(f"{repo_user}/{repo_name}")

    def get_labels(self) -> list[Label]:
        return list(self._repo_handle.get_labels())
    
    def set_labels(self, labels: list[Label]):
        for label in labels:
            self._repo_handle.create_label(label.name, label.color)

    def remove_labels(self, labels: list[str]):
        existing_labels_map = {l.name: l for l in self.get_labels()}

        for label in labels:
            label_obj = existing_labels_map.get(label)
            if label_obj:
                label_obj.delete()
