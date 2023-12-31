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
from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler.actions import PostLabellingAction
from autolabeler.labelers import LabelParams


LOG = logging.getLogger(__name__)


class BaseLabelsTarget(metaclass=abc.ABCMeta):
    """ ABC for GitHub API entities which can have labels applies to them. """

    @abc.abstractmethod
    def get_labels(self) -> list[LabelParams]:
        return NotImplemented

    @abc.abstractmethod
    def set_labels(self, labels: list[LabelParams]):
        _ = labels
        return NotImplemented

    @abc.abstractmethod
    def remove_labels(self, labels: list[str]):
        _ = labels
        return NotImplemented

    @abc.abstractmethod
    def perform_action(self, action: PostLabellingAction) -> bool:
        """ Performs the given action or returns False if already in desired state. """
        _ = action 
        return NotImplemented

    @abc.abstractmethod
    def add_comment(self, comment: str):
        _ = comment
        return NotImplemented

    @abc.abstractmethod
    def get_target_handle(self):
        return NotImplemented


class RepoLabelsTarget(BaseLabelsTarget):
    """ Abstracts the creation/deletion of labels on Repositories. """

    def __init__(self, client: Github, repo_user: str, repo_name: str):
        if not all([repo_name, repo_user]):
            raise ValueError(
                "Target repository user and name arguments must be provided. "
                f"Got: {repo_name=} {repo_name=}")

        self._client = client
        self._user = repo_user
        self._name = repo_name
        self._repo = client.get_repo(f"{repo_user}/{repo_name}")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self._user}/{self._name}')"

    def get_target_handle(self):
        return self._repo

    def get_labels(self) -> list[LabelParams]:
        return [LabelParams.from_label(l) for l in self._repo.get_labels()]

    def perform_action(self, action: PostLabellingAction) -> bool:
        raise Exception(f"{self}: Cannot perform '{action}' on Repository.")

    def add_comment(self, comment: str):
        raise Exception(f"{self}: Cannot comment on Repository: {comment}")

    def set_labels(self, labels: list[LabelParams]):
        """ Creates or updates label defs on repo. """
        existing_labels_map = {l.name: l for l in self._repo.get_labels()}

        labels_to_update = [
            l for l in labels
            if l.name in existing_labels_map
            and l != LabelParams.from_label(existing_labels_map[l.name])]
        LOG.info(
            f"Updating following labels on repo {self._user}/{self._name}: "
            f"{labels_to_update}")
        for l in labels_to_update:
            elabel = existing_labels_map[l.name]
            elabel.edit(l.name, l.color, l.description)
            elabel.update()
            LOG.debug(
                f"Updated repo {self._user}/{self._name} label {elabel} to: {l}")

        labels_to_create = [
            l for l in labels if l.name not in existing_labels_map]
        for l in labels_to_create:
            self._repo.create_label(l.name, l.color, l.description)

    def remove_labels(self, labels: list[str]):
        """ Removes labels with the selected name. """
        existing_labels_map = {l.name: l for l in self._repo.get_labels()}
        existing_label_names = set(existing_labels_map.keys())

        label_name_set = set(labels)
        missing = label_name_set.difference(existing_label_names)
        if missing:
            msg = (
                f"Requested deletion of repo {self._user}/{self._name} for "
                f"non-existing labels: {missing}")
            # TODO(aznashwan): optionally raise based on kwarg.
            LOG.warn(msg)

        to_delete = label_name_set.intersection(existing_label_names)
        LOG.info(
            f"Deleting following labels on repo {self._user}/{self._name}: "
            f"{to_delete}")
        for name in to_delete:
            existing_labels_map[name].delete()
            LOG.debug(f"Deleted repo {self._user}/{self._name} label '{name}'")



class ObjectLabellingTarget(BaseLabelsTarget):
    """ Abstracts managing pre-existing repo labels on repo objects like PRs and Issues. """

    def __init__(self, client: Github, repo_user: str, repo_name: str,
                 # NOTE(aznashwan): repo items IDs all start from 1:
                 repo_item_type: str, repo_item_id):
        if not all([repo_user, repo_name, repo_item_type, repo_item_id]):
            raise ValueError(
                "All arguments must be provided, got: "
                f"{repo_name=} {repo_name=} {repo_item_type=} {repo_item_id=}")

        self._client = client
        self._user = repo_user
        self._repo = repo_name
        self._item_type = repo_item_type
        self._item_id = repo_item_id
        self._target_obj = self._get_target_obj()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self._get_target_resource_path()}')"

    def get_target_handle(self):
        return self._target_obj

    def _get_target_resource_path(self):
        return f"{self._user}/{self._repo}/{self._item_type}/{self._item_id}"

    def _get_target_obj(self) -> Issue|PullRequest:
        repo = self._client.get_repo(f"{self._user}/{self._repo}")

        target = repo
        match self._item_type:
            case _ if not self._item_type:
                raise ValueError(f"{self._item_type=}")
            case "issue" | "issues":
                if self._item_id:
                    target = repo.get_issue(self._item_id)
                    if target.pull_request:
                        LOG.warn(
                            f"Provided target {self._get_target_resource_path()} is "
                            f"is actually a Pull Request. Treating as such.")
                        self._item_type = "pull"
                        target = target.as_pull_request()
                else:
                    return NotImplemented
            case "pull" | "pulls":
                if self._item_id:
                    target = repo.get_pull(self._item_id)
                else:
                    return NotImplemented
            case _:
                raise ValueError(
                    f"Unsupported repo labelling target: {self._item_type}")

        return target

    def _get_repo(self) -> Repository:
        if isinstance(self._target_obj, PullRequest):
            return self._target_obj.as_issue().repository
        return self._target_obj.repository

    def _ensure_repo_labels_exist(self, labels: list[LabelParams]) -> list[Label]:
        repo = self._get_repo()
        existing_labels_map = {l.name: l for l in repo.get_labels()}

        missing = []
        for label in labels:
            if label.name in existing_labels_map:
                elabel = LabelParams.from_label(existing_labels_map[label.name])
                if label != elabel:
                    LOG.warning(
                        f"Found mismatched label definition for {label} while "
                        f"applying to {self._target_obj}: {elabel}")
            else:
                missing.append(label)

        if missing:
            repo_labeler = RepoLabelsTarget(self._client, self._user, self._repo)
            LOG.info(
                f"Creating following new labels on repo {repo} for {self._target_obj}: {missing}")
            repo_labeler.set_labels(missing)

        # NOTE: refetch all repo labels:
        return [l for l in repo.get_labels()]

    def _change_state(self, state: str) -> bool:
        """ Returns whether the state needed to be changed or not. """
        current = self._target_obj.state
        if current != state:
            self._target_obj.edit(state=state)
            LOG.info(f"{self}: transitioning from {current} to {state}")
            self._target_obj.update()
            return True
        LOG.info(f"{self._target_obj} is now {state}")
        return False

    def get_labels(self) -> list[LabelParams]:
        return [LabelParams.from_label(l) for l in self._target_obj.get_labels()]

    def perform_action(self, action: PostLabellingAction) -> bool:
        match action:
            case PostLabellingAction.OPEN:
                return self._change_state('open')
            case PostLabellingAction.CLOSE:
                return self._change_state('closed')
            case other:
                raise NotImplementedError(
                    f"{self}.perform_action(): unsupported action: '{other}'")

    def add_comment(self, comment: str):
        obj = self._target_obj
        if isinstance(self._target_obj, PullRequest):
            obj = self._target_obj.as_issue()

        obj.create_comment(comment)  # pyright: ignore

    def set_labels(self, labels: list[LabelParams]):
        label_objects_map = {l.name: l for l in self._ensure_repo_labels_exist(labels)}

        # NOTE(aznashwan): `set_labels()` overwrites the whole label list,
        # so we must union the label sets ourselves.
        old_labels = self.get_labels()
        label_names_to_add = {l.name for l in old_labels}.union(
            {l.name for l in labels})

        if label_names_to_add:
            LOG.info(f"Adding following labels to "
                     f"{self._get_target_resource_path()}: {label_names_to_add}")
            labels_to_add = [
                label_objects_map[l]
                for l in label_names_to_add]
            self._target_obj.set_labels(*labels_to_add)

    def remove_labels(self, labels: list[str]):
        existing_labels_map = {l.name: l for l in self._target_obj.get_labels()}
        for label in labels:
            label_obj = existing_labels_map.get(label)
            if label_obj:
                label_obj.delete()
                LOG.info(f"Deleted label: {label_obj}")
