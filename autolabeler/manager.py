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

from github import Github
from github.Issue import Issue
from github.PullRequest import PullRequest

from autolabeler import labelers
from autolabeler.labelers import LabelParams
from autolabeler import targets


LOG = logging.getLogger(__name__)


class LabelsManager():

    def __init__(self, client: Github, target_str: str, labelers_config: dict):
        """ Manages labels on the Github resource with the provided path and config.

        Supports inputs of the form:
        - username/repository_name
        - user/repo/{issue/pull}/123 for a specific issue/pull
            "repo": "<name of the repository>",
            "type": None/"issue"/"pull",
            "id": None/int,
        }
        """
        # TODO(aznashwan): handle full URLs.
        # TODO(aznashwan): user/repo/{issues/pulls} for all issues/pulls
        self._client = client
        self._target_str = target_str
        self._labelers_config = labelers_config
        self._labelers = labelers.load_labelers_from_config(labelers_config)

        # HACK(aznashwan): find better way to call appropriate labelling func.
        self._labelling_operation = None

        parts = target_str.split('/')
        if len(parts) not in range(2, 5):
            raise ValueError(
                "Target format must be a slash-separated string with the "
                "following path elements: username/repository[/type[/id]]. "
                f"Got: {target_str} ({len(parts)} path elements)")

        def _safe_index(n: int):
            try:
                return parts[n]
            except IndexError:
                return None

        target_id = 0
        match _safe_index(3):
            case None:
                pass
            case some:
                target_id = int(some)

        user = parts[0]
        repo = parts[1]
        target_type = _safe_index(2)
        self._labelling_target = targets.RepoLabelsTarget(client, user, repo)
        match target_type:
            case None | "":
                self._labelling_operation = lambda l, t: l.get_labels_for_repo(t)
            case 'issue' | 'pull':
                # NOTE(aznashwan): `ObjectLabellingTarget` can handle both
                # issues and PRs transparently:
                self._labelling_target = targets.ObjectLabellingTarget(
                    client, user, repo, target_type, target_id)
                target = self._labelling_target.get_target_handle()
                if isinstance(target, PullRequest):
                    self._labelling_operation = lambda l, t: l.get_labels_for_pr(t)
                elif isinstance(target, Issue):
                    self._labelling_operation = lambda l, t: l.get_labels_for_issue(t)
                else:
                    raise ValueError(
                        f"Unsupported target handle: {target} ({type(target)})")
            case 'issues' | 'pulls':
                raise NotImplementedError(
                    "Multi issue/PR currently unsupported.")
            case other:
                accepted = ['pull(s)', 'issue(s)']
                raise ValueError(
                    f"Unsupported target type '{other}'. Must be one of: {accepted}")

    def generate_labels(self) -> list[LabelParams]:
        """ Generates labels based on the provided rules for the target. """
        labels = []
        for labeler in self._labelers:
            labels.extend(
                self._labelling_operation(
                    labeler, self._labelling_target.get_target_handle()))  # pyright: ignore
        return labels

    def sync_labels(self) -> list[LabelParams]:
        """ Applies all labels to the target, updating them if need be. """
        labels = self.generate_labels()
        LOG.info(f"Applying following labels to {self._target_str}: {labels}")
        self._labelling_target.set_labels(labels)
        return labels

    def run_post_labelling_actions(self):
        for labeler in self._labelers:
            labeler.run_post_labelling_actions(
                self._labelling_target.get_target_handle())  # pyright: ignore

    def remove_undefined(self):
        """ Deletes all labels which are not defined in the config from the target. """
        existing_labels = self._labelling_target.get_labels()
        generated_label_names = [l.name for l in self.generate_labels()]
        undefined = [
            l.name for l in existing_labels if l not in generated_label_names]
        if undefined:
            LOG.info(
                f"Removing following undefined labels from {self._target_str}: "
                f"{undefined}")
            self._labelling_target.remove_labels(undefined)
