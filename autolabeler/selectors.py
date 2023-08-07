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
import typing
from typing import Self

from github.ContentFile import ContentFile
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import utils


LOG = utils.getStdoutLogger(__name__)

MATCH_RESULT_FIELD_REGEX = re.compile(r'^[a-zA-Z_]\w*$')

FileContainingObject = typing.Union[Repository, PullRequest]


class MatchResult(dict):
    """ Simple wrapper around a dict allows dot access on fields. """
    def __init__(self, d: dict|None=None, reference_key: str|None=None):
        super().__init__()
        self._reference_key = reference_key

        if d is None:
            d = {}
        for key, value in d.items():
            key_name = str(key)
            if not self._check_key_name(key_name):
                raise ValueError(
                    f"Unacceptable dict key '{key_name}' for attribute access dict")
            self[key_name] = MatchResult(value) if type(value) is dict else value

    def get_reference_value(self) -> object|None:
        """ Returns a value from the matches to use to crossref other matches. """
        if not self._reference_key:
            return None

        accesses = self._reference_key.split(".")
        curr = self
        for access in accesses:
            curr = curr[access]
        return curr

    def _check_key_name(self, key: str) -> bool:
        return bool(MATCH_RESULT_FIELD_REGEX.findall(key))

    def __getattr__(self, key):
        if key in self:
            return self[key]
        raise AttributeError(f"'{key}' is not defined in: {self}")


class Selector(metaclass=abc.ABCMeta):

    @abc.abstractclassmethod
    def from_dict(cls, val: dict) -> Self:
        _ = val
        return NotImplemented

    @abc.abstractclassmethod
    def get_selector_name(cls):
        return NotImplemented

    @abc.abstractmethod
    def match(self, obj: object, file_cache: list[ContentFile]=[]) -> list[MatchResult]:
        """ Returns a list of matches for the Github object. """
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

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        title = self._re_title
        desc = self._re_description[:16]
        comments = self._re_comments
        maintainers_only = self._re_maintainer_comments
        case_insensitive = self._case_insensitive
        return (
            f"{cls}({title=}, {desc=}, {comments=}, {maintainers_only=}, "
            f"{case_insensitive=})")

    @classmethod
    def get_selector_name(cls):
        return "regex"

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
            match = _get_match_groups(
                self._re_title, obj.title, case_insensitive=self._case_insensitive)
            if match:
                res["title"] = match

        if self._re_description and obj.body:
            match = _get_match_groups(
                self._re_description, obj.body, case_insensitive=self._case_insensitive)
            if match:
                res["description"] = match

        return res

    def _get_repo_for_target_object(self, obj: Issue|PullRequest) -> Repository:
        if isinstance(obj, Issue):
            return obj.repository
        return obj.as_issue().repository

    def _check_comment_role(
            self, repo: Repository, comment: IssueComment, role: str) -> bool:
        return repo.get_collaborator_permission(comment.user) == role

    def _get_comment_matches(self, obj: Issue|PullRequest) -> list[dict]:
        if not self._re_comments:
            return []

        comments = []
        if isinstance(obj, Issue):
            comments = [c for c in obj.get_comments()]
        else:
            # TODO(aznashwan): handle Review Comments too.
            comments = [c for c in obj.as_issue().get_comments()]

        res = []
        repo = self._get_repo_for_target_object(obj)
        for comm in comments:
            if not self._check_comment_role(repo, comm, 'admin'):
                LOG.debug(
                    f"Skipping non-maintainer comment {comm.id} on {obj}.")
                continue
            match = _get_match_groups(
                self._re_comments, comm.body,
                case_insensitive=self._case_insensitive)
            if match:
                res.append(match)

        return res

    def match(self, obj: object, _: list[ContentFile]=[]) -> list[MatchResult]:
        """ Matches comments on Issues/PRs.

        Returns list of matches of the form: {
            "case_insensitive": "<case_insensitive setting>",
            "maintainer_comments_only": "<maintainer comment flag>",
            "title": {
                full: "<full title str>",
                match: "<title part that matched>",
                groups: ["list", "of", "match", "groups"],
            },
            "description": { <same regex math structure as title> },
            "comment": { <same regex math structure as title> },
        }
        """
        # NOTE(aznashwan): Only Issues/PRs have the concept of comments.
        if not isinstance(obj, (Issue, PullRequest)):
            LOG.warn(
                f"RegexSelector got unsupported object type {type(obj)}: {obj}")
            return []

        res = []
        tdms = self._get_title_description_matches(obj)
        comms = self._get_comment_matches(obj)
        # Update every comment match result with the title/description matches.
        for comment_match in comms:
            comment_match.update(tdms)

        if not res and tdms:
            res = [tdms]

        return [MatchResult(m) for m in res]


class FilesSelector(Selector):
    """ Selects Repos/PRs based on contained file properties. """

    def __init__(self, file_name_re: str="", file_type: str=""):
        self._file_type = file_type
        self._file_name_re = file_name_re

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        name_regex = self._file_name_re
        return f"{cls}({name_regex=})"

    @classmethod
    def get_selector_name(cls):
        return "files"

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
              file_cache: list[str]=[]) -> list[MatchResult]:
        """ Returns list of results containing the following fields: {
            name_regex: {
                full: "full/path/to/file",
                match: "<only the matched part>",
                groups: ["list", "of", "match", "groups"],
            },
            type: {
                expected: "<expected type>",
                actual: "<actual filetype>",
            }
        }
        """
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
            new = {}
            ref = None
            if self._file_name_re:
                match = _get_match_groups(self._file_name_re, path)
                if match:
                    new["name_regex"] = match
                    ref = "name_regex.full"
            if self._file_type:
                raise NotImplementedError(
                    "Filetype selection not yet implemented.")
            if new:
                res.append(MatchResult(new, reference_key=ref))

        return res


class DiffSelector(Selector):

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

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        min = self._min
        max = self._max
        change_type = self._change_type
        return f"{cls}({min=}, {max=}, {change_type=})"

    @classmethod
    def get_selector_name(cls):
        return "diff"

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
              file_cache: list[str]=[]) -> list[MatchResult]:
        """ Returns a single match result of the form: {
            "target_type": "<what 'type' option the selector had set>"
            "min": "<the min parameter or -Inf if not set>",
            "max": "<the max parameter or +Inf if not set>",
            "total": "<total lines changed for the PR>",
            "additions": "<total lines added for the PR>",
            "deletions": "<total deletions for the PR>",
            "net": "<additions - deletions for PR>",
            "files": {  # NOTE: dict with diffs for every file in PR.
                "<full_filepath_1>": {
                    "total": "<total>",
                    "additions": "<additions>",
                    "deletions": "<deletions>",
                    "net": "<additions - deletions>"
                }
            }
        }
        """
        _ = file_cache

        # NOTE(aznashwan): Only Repositories/PRs have associated files.
        if not isinstance(obj, (PullRequest, Repository)):
            LOG.warn(
                f"{self.__class__}.match() got unsupported object type {type(obj)}: {obj}")
            return []

        res = {
            "min": self._min if self._min is not None else "-Inf",
            "max": self._max if self._max is not None else "+Inf"}
        if isinstance(obj, Repository):
            # TODO(aznashwan): ideally pre-define the labels on repos here.
            return [MatchResult(res)]

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
            "total": obj.additions + obj.deletions,
            "additions": obj.additions,
            "deletions": obj.deletions,
            "net": obj.additions - obj.deletions})

        files = {}
        lister = FileLister(obj)
        for filepath, file in lister.list_file_paths():
            files[filepath] = {
                "total": file.additions + file.deletions,
                "additions": file.additions,
                "deletions": file.deletions,
                "net": file.additions - file.deletion}
        res["files"] = files  # pyright: ignore

        return [MatchResult(res)]


class FileLister():
    """ Lists changed files for PRs or whole file tree for Repos. """

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

    def list_file_paths(self) -> dict:
        if isinstance(self._obj, Repository):
            return {
                f.path: f for f in self._list_files_from_repo(self._obj, "")}
        elif isinstance(self._obj, PullRequest):
            return {
                f.filename: f for f in self._list_files_from_pr(self._obj)}
        raise TypeError(
            f"Can't list files for object {self._obj} ({type(self._obj)})")


SELECTOR_CLASSES = [
    RegexSelector,
    FilesSelector,
    DiffSelector,
]


def get_selector_cls(selector_name: str, raise_if_missing: bool=True) -> typing.Type:
    selector_name_map = {s.name: s for s in SELECTOR_CLASSES}
    selector = selector_name_map.get(selector_name)

    if not selector:
        msg = (
            f"Unknown selector type '{selector_name}'. Supported selectors are: "
            f"{list(selector_name_map.keys())}")
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
        regex: str, value: str, case_insensitive=False) -> dict|None:
    """ On regex match, returns a dict of the form: {
        full: "<full string which contained a match>",
        match: "<only the matched part>",
        groups: ["list", "of", "match", "groups"],
    }
    """
    flags = []
    if case_insensitive:
        flags = [re.IGNORECASE]

    match = re.search(regex, value, *flags)
    if not match:
        return None

    res = {
        "full": match.string,
        "match": match.group(),
        "groups": list(match.groups()),
    }
    # for i, group in enumerate(match.groups()):
    #     res[f"group_{i}"] = group

    LOG.debug(f"Match result for {regex=} to {value=}: {res}")
    return res
