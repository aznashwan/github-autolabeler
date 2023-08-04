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
import re
from typing import Self
import typing

from github.ContentFile import ContentFile
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import utils


LOG = utils.getStdoutLogger(__name__)

FileContainingObject = typing.Union[Repository, PullRequest]


class Selector(metaclass=abc.ABCMeta):

    @abc.abstractclassmethod
    def from_dict(cls, val: dict):
        _ = val
        return NotImplemented

    @abc.abstractmethod
    def match(self, obj: object, file_cache: list[ContentFile]=[]) -> list[dict]:
        """ Returns a dict of str key-values if the Github object is matched.

        Returned keys should follow a predictable namespacing model based on the
        checked property, so for example if a selector has "check1"="<some checks>",
        the returned dict should contain: {
            "check1": "<value of match for check1>"
        }
        """
        return NotImplemented


class RegexSelector(Selector):
    """ Selects Issues/PRs based on regexes of their title/comments. """

    def __init__(self,
                 re_title: str="", re_description: str="",
                 re_comments: str="", maintainer_comments_only: bool=True,
                 case_insensitive: bool=False):
        self._re_title = re_title
        self._re_description = re_description
        self._re_comments = re_comments
        self._re_maintainer_comments = maintainer_comments_only
        self._case_insensitive = case_insensitive

    @classmethod
    def from_dict(cls, val: dict) -> Self:
        """ Supports dict with following keys:

        case-insensitive: bool,
        title: "<regex for title>",
        description: "<regex for description>",
        comments: "<regex for comments>",
        maintainer-comments-only: bool,
        """
        supported_keys = [
            "case-insensitive", "title", "description",
            "comments", "maintainer-comments", "maintainer-comments-only"]
        if not any(k in val for k in supported_keys):
            raise ValueError(
                f"FileRegexSelector requires at least one regex key "
                f"({supported_keys}). Got {val}")

        kwargs = {
            "case_insensitive": val.get("case-insensitive", False),
            "re_title": val.get("title", ""),
            "re_description": val.get("description", ""),
            "re_comments": val.get("comments", ""),
            "maintainer_comments_only": val.get(
                "maintainer-comments-only", True)}

        return cls(**kwargs)

    def _get_title_description_matches(self, obj: PullRequest|Issue) -> dict:
        res = {}

        if self._re_title and obj.title:
            res.update(
                _get_match_groups(
                    self._re_title, obj.title, prefix="title-",
                    case_insensitive=self._case_insensitive))

        if self._re_description and obj.body:
            res.update(
                _get_match_groups(
                    # NOTE: PyGithub refers to issue/PR descriptions as their "body".
                    self._re_description, obj.body, prefix="description-",
                    case_insensitive=self._case_insensitive))

        return res

    def _get_comment_matches(self, obj: Issue|PullRequest) -> list[dict]:
        if not self._re_comments:
            return []

        comments = []
        if isinstance(obj, Issue):
            comments = [c.body for c in obj.get_comments()]
        else:
            # TODO(aznashwan): handle Review Comments too.
            comments = [c.body for c in obj.as_issue().get_comments()]

        res = []
        for comm in comments:
            res.append(_get_match_groups(
                # NOTE: PyGithub refers to issue/PR descriptions as their "body".
                self._re_comments, comm, prefix="regex-comments-",
                case_insensitive=self._case_insensitive))

        return res

    def match(self, obj: object, _: list[ContentFile]=[]) -> list[dict]:
        # NOTE(aznashwan): Only Issues/PRs have the concept of comments.
        if not isinstance(obj, (Issue, PullRequest)):
            LOG.warn(
                f"RegexSelector got unsupported object type {type(obj)}: {obj}")
            return []

        # TODO(aznashwan): move this into `_get_comment_matches()`?
        if self._re_maintainer_comments:
            repo = None
            if isinstance(obj, Issue):
                repo = obj.repository
            else:
                repo = obj.as_issue().repository

            if repo.get_collaborator_permission(obj.user) != 'admin':
                LOG.info(
                    f"Avoiding setting label from non-maintainer {obj.user}")
                return []

        res = self._get_comment_matches(obj)
        res.append(self._get_title_description_matches(obj))

        return [m for m in res if m]


class FilesSelector(Selector):
    """ Selects Repos/PRs based on contained file properties. """

    def __init__(self, file_name_re: str="", file_type: str=""):
        self._file_type = file_type
        self._file_name_re = file_name_re

    @classmethod
    def from_dict(cls, val: dict) -> Self:
        supported_keys = ["name-regex", "type"]
        if not any(k in val for k in supported_keys):
            raise ValueError(
                f"FilesSelector requires at least one options key "
                f"({supported_keys}). Got {val}")

        kwargs = {
            "file_name_re": val.get("name-regex", ""),
            "file_type": val.get("type", "")}

        return cls(**kwargs)

    def match(self, obj: Repository|PullRequest,
              file_cache: list[str]=[]) -> list[dict]:
        # NOTE(aznashwan): Only Repositories/PRs have associated files.
        if not isinstance(obj, (Repository, PullRequest)):
            LOG.warn(
                f"{self.__class__}.match() got unsupported object type {type(obj)}: {obj}")
            return []

        all_files = file_cache
        if not all_files:
            lister = FileLister(obj)
            all_files = lister.list_file_paths()

        res = []
        for path in all_files:
            match = _get_match_groups(self._file_name_re, path)
            match = {f"files-name-regex-{k}": match[k] for k in match}
            if match:
                res.append(match)

        return res


class LinesChangedSelector(Selector):

    def __init__(
            self, min: int|None=None, max: int|None=None,
            change_type: str="total"):
        if min is None and max is None:
            raise ValueError(
                f"{self.__class__}: at least one of min/max is required.")
        self._min = min
        self._max = max
        supported_change_types = [
            "additions", "deletions", "total", "net"]
        if change_type not in supported_change_types:
            raise ValueError(
                f"Unsupported change type {change_type}. "
                f"Must be one of: {supported_change_types}")
        self._change_type = change_type

    @classmethod
    def from_dict(cls, val: dict):
        supported_keys = ["min",  "max", "type"]
        unsupported_keys = [k for k in val if k not in supported_keys]
        if unsupported_keys:
            raise ValueError(
                f"{cls}.from_dict() got unsupported keys: {unsupported_keys}")
        return cls(
            min=val.get("min"), max=val.get("max"),
            change_type=val.get("type", "total"))

    def match(self, obj: Repository|PullRequest,
              file_cache: list[str]=[]) -> list[dict]:
        _ = file_cache

        # NOTE(aznashwan): Only Repositories/PRs have associated files.
        if not isinstance(obj, (PullRequest, Repository)):
            LOG.warn(
                f"{self.__class__}.match() got unsupported object type {type(obj)}: {obj}")
            return []

        res = {
            "diff-min":
                self._min if self._min is not None else "-Inf",
            "diff-max":
                self._max if self._max is not None else "+Inf"}
        if isinstance(obj, Repository):
            # TODO(aznashwan): ideally pre-define the labels on repos here.
            return [res]

        changes = 0
        match self._change_type:
            case "total":
                changes = obj.additions + obj.deletions
            case "additions":
                changes = obj.additions
            case "deletions":
                changes = obj.deletions
            case "net":
                changes = obj.additions - obj.deletions
            case other:
                raise ValueError(
                    f"Got unsupported change totalling scheme: {other}")

        if self._min is not None and changes < self._min:
            return []
        if self._max is not None and changes >= self._max:
            return []

        res.update({
            "diff-total": obj.additions + obj.deletions,
            "diff-additions": obj.additions,
            "diff-deletions": obj.deletions,
            "diff-net": obj.additions - obj.deletions})

        return [res]


class FileLister():

    def __init__(self, obj: Repository|PullRequest):
        self._obj = obj

    def _list_files_from_repo(self, repo: Repository, path: str=""):
        files = []
        for item in repo.get_contents(path):  # pyright: ignore
            if item.type == "dir":
                files.extend(self._list_files_from_repo(repo, item.path))
            else:
                files.append(item)

        return files

    def _list_files_from_pr(self, pr: PullRequest):
        return list(pr.get_files())

    def list_file_paths(self) -> list[str]:
        if isinstance(self._obj, Repository):
            return [f.path for f in self._list_files_from_repo(self._obj, "")]
        elif isinstance(self._obj, PullRequest):
            return [f.filename for f in self._list_files_from_pr(self._obj)]
        raise TypeError(
            f"Can't list files for object {self._obj} ({type(self._obj)})")




SELECTORS_NAME_MAP = {
    "regex": RegexSelector,
    "files": FilesSelector,
    "diff": LinesChangedSelector,
}


def get_selector_cls(selector_name: str, raise_if_missing: bool=True) -> typing.Type:
    selector = SELECTORS_NAME_MAP.get(selector_name)

    if not selector:
        msg = (
            f"Unknown selector type '{selector_name}'. Supported selectors are: "
            f"{list(SELECTORS_NAME_MAP.keys())}")
        if raise_if_missing:
            raise ValueError(msg)
        else:
            LOG.warn(msg)

    return selector


def list_files_for_repo(obj: Repository, path: str="") -> list[ContentFile]:
    return list(obj.get_contents(path))  # pyright: ignore


def list_all_files_for_repo(obj: Repository, path="") -> list[ContentFile]:
    files = []
    for item in list_files_for_repo(obj, path=path):
        if item.type == "dir":
            files.extend(list_all_files_for_repo(obj, path=item.path))
        else:
            files.append(item)

    return files


def _get_match_groups(
        regex: str, value: str, prefix="", case_insensitive=False) -> dict:
    """ Returns dict with all match groups and keys of the form 'prefix-group-N'"""
    res = {}
    flags = []
    if case_insensitive:
        flags = [re.IGNORECASE]

    match = re.search(regex, value, *flags)
    if match:
        res[f"{prefix}group-0"] = value
        for i, group in enumerate(match.groups()):
            res[f"{prefix}group-{i+1}"] = group

    LOG.debug(f"Match result for {regex=} to {value=}: {res}")

    return res
