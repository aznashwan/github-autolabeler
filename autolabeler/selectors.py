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
import math
import re
import typing
from typing import Self

from github.ContentFile import ContentFile
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Repository import Repository


LOG = logging.getLogger(__name__)

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
                LOG.warning(
                    f"Unacceptable dict key '{key_name}' for attribute access dict."
                    " It will require accessing by dictionary key indexing.")
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
    def from_val(cls, val: dict, extra: object|None=None) -> Self:
        """ Load the selector from a value. """
        _ = val
        _ = extra
        return NotImplemented

    @abc.abstractclassmethod
    def get_selector_name(cls):
        return NotImplemented

    @abc.abstractmethod
    def match(self, obj: object) -> list[MatchResult]:
        """ Returns a list of matches for the Github object. """
        return NotImplemented


class BaseRegexSelector(Selector):
    """ Returns a match based on a provided regexes. """

    def __init__(self, regexes: list[str], case_insensitive: bool=False):
        self._regexes = regexes
        self._case_insensitive = case_insensitive

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Accepts:
        str: the regex
        list: list of regexes
        """
        # TODO(aznashwan): support explicit dict loading too.
        if not extra:
            extra = {}
        regexes = [".*"]
        match val:
            case val if isinstance(val, str):
                regexes = [val]
            case val if isinstance(val, list):
                not_strings = [v for v in val if not isinstance(v, str)]
                if not_strings:
                    raise TypeError(
                        f"{cls.__name__}.from_val({val}): unacceptable value "
                        "of elements in list definition. All items must "
                        f"str. Non-str items were: {not_strings}")
                regexes = val
            case other:
                raise TypeError(
                    f"{cls.__name__}.from_val({other}): unacceptable value "
                    f"of type {type(other)}: must be str or list[str].")

        return cls(
            regexes=regexes,
            case_insensitive=extra.get('case_insensitive', False))

    def __repr__(self) -> str:
        regexes = self._regexes
        case_insensitive = self._case_insensitive
        return f"{self.__class__.__name__}({regexes=}, {case_insensitive=})"

    @abc.abstractmethod
    def _get_items_to_match(selfi, obj: object) -> list[dict]:
        """ Should return a list of dicts of the form: [{
            "string": "<the string to match>",
            "meta": {
                "kvps": "to attach to the match"
            }
        }]
        """
        raise NotImplementedError()

    def match(self, obj: object) -> list[MatchResult]:
        """ Returns all items based on the implementation of '_get_items_to_match'
        which match ALL the regexes.

        Returns list of matches of the form: [{
            "case_insensitive": "<case_insensitive setting>",
            "full": "<full string which matched ALL regexes>",
            "match": "<match part for the first regex (same as match0)>",
            "matchN": "<match part for the Nth regex starting from 0>",
            "groups": ["list of", "match groups for the first regex (~= groups0)"],
            "groupsN": ["list of", "match groups for the Nth regex"],
            "<metafield>": "arbitrary metafields returned by _get_items_to_match."
        }]
        """
        if not self._regexes:
            LOG.warning(f"{self}.match({obj}): no regexes defined.")
            return []

        res = []
        for item in self._get_items_to_match(obj):
            matches = {}
            string = item['string']
            for regex in self._regexes:
                match = _get_match_groups(
                    regex, string,
                    case_insensitive=self._case_insensitive)

                meta = item.get('meta')
                if match and meta:
                    match.update(meta)

                matches[regex] = match

            if any(m is None for m in matches.values()):
                LOG.debug(
                    f"{self}.match({obj}): one or more regexes failed to "
                    f"match string '{string}': {matches}")
                continue

            first = matches[self._regexes[0]]
            new = {
                "case_insensitive": self._case_insensitive,
                "full": item,
                "match": first["match"],
                "groups": first["groups"]}

            for i, r in enumerate(matches):
                m = matches[r]
                new.update({
                    f"match{i}": m["match"],
                    f"groups{i}": matches[r]["groups"]})
            res.append(new)

        return [MatchResult(m) for m in res]


class TitleRegexSelector(BaseRegexSelector):
    """ Checks the title of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "author": "<ID of user who made the PR/issue.>",
        "created_at": <Python datetime object when the Issue/PR was created>
        "all": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "title"

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.title or "NOTITLE",
            "meta": {
                "author": obj.user.login,
                "created_at": obj.created_at,
            }
        }]


class DescriptionRegexSelector(BaseRegexSelector):

    """ Checks the description of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "author": "<ID of user who made the PR/issue.>",
        "created_at": <Python datetime object when the Issue/PR was created>
        "all": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "description"

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.body or "NODESCRIPTION",
            "meta": {
                "author": obj.user.login,
                "created_at": obj.created_at,
            }
        }]


class BaseCommentsRegexSelector(BaseRegexSelector):

    """ Checks the comments of issues/PRs based on the given regex and factors.

    Returns one match per comment of the form: [{
        "id": "<ID of the comment>",
        "user": "<ID of user who made the comment.>",
        "user_role": "<Role of the user on the repo>",
        "created_at": <Python datetime object when the Issue/PR was created>,
        "all": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    __ROLES = []

    def __init__(
            self, regexes: list[str],
            case_insensitive: bool=False,
            user_roles_cache: dict|None=None):
        super().__init__(regexes=regexes, case_insensitive=case_insensitive)
        self._user_roles_cache = user_roles_cache or {}

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Accepts:
        str: the regex
        list: list of regexes
        """
        # TODO(aznashwan): support explicit dict loading too.
        if not extra:
            extra = {}
        regexes = [".*"]
        match val:
            case val if isinstance(val, str):
                regexes = [val]
            case val if isinstance(val, list):
                not_strings = [v for v in val if not isinstance(v, str)]
                if not_strings:
                    raise TypeError(
                        f"{cls.__name__}.from_val({val}): unacceptable value "
                        "of elements in list definition. All items must "
                        f"str. Non-str items were: {not_strings}")
                regexes = val
            case other:
                raise TypeError(
                    f"{cls.__name__}.from_val({other}): unacceptable value "
                    f"of type {type(other)}: must be str or list[str].")

        return cls(
            regexes=regexes,
            case_insensitive=extra.get('case_insensitive', False))

    def __repr__(self) -> str:
        regexes = self._regexes
        case_insensitive = self._case_insensitive
        user_roles = self.__ROLES
        return (
            f"{self.__class__.__name__}("
            f"{regexes=}, {case_insensitive=}, {user_roles=})")

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        repo = None
        comments = []
        if isinstance(obj, Issue):
            repo = obj.repository
            comments = [c for c in obj.get_comments()]
        else:
            # TODO(aznashwan): handle Review Comments too.
            as_issue = obj.as_issue()
            repo = as_issue.repository
            comments = [c for c in as_issue.get_comments()]

        res = []
        for comm in comments:
            role = ""
            if self.__ROLES:
                uid = comm.user.login
                role = self._user_roles_cache.get(uid)
                if not role:
                    role = repo.get_collaborator_permission(uid)
                    self._user_roles_cache[uid] = role

                if role not in self.__ROLES:
                    LOG.debug(
                        f"Skipping comment {comm.id} as author {uid} with "
                        f"role {role} is not a {self.__ROLES}")
                    continue

            res.append({
                "string": comm.body or "NOBODY",
                "meta": {
                    "id": comm.id,
                    "user": comm.user.login,
                    "user_role": role,
                    "created_at": comm.created_at}})

        return res


class ContributorCommentsRegexSelector(BaseCommentsRegexSelector):
    """ Matches comments from any contributor on Issues/PRs. """

    __ROLES = []  # pyright: ignore

    @classmethod
    def get_selector_name(cls) -> str:
        return "comments"


class MaintainerCommentsRegexSelector(BaseCommentsRegexSelector):
    """ Matches comments from any maintainers on Issues/PRs. """

    __ROLES = ['admin']  # pyright: ignore

    @classmethod
    def get_selector_name(cls) -> str:
        return "maintainer_comments"


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
    def from_val(cls, val: dict) -> Self:
        supported_keys = ["name_regex", "type"]
        if not any(k in val for k in supported_keys):
            raise ValueError(
                f"FilesSelector requires at least one options key "
                f"({supported_keys}). Got {val}")

        kwargs = {
            "file_name_re": val.get("name_regex", ""),
            "file_type": val.get("type", "")}

        return cls(**kwargs)

    def match(self, obj: Repository|PullRequest) -> list[MatchResult]:
        # TODO(aznashwan): make it return list of file objects.
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
            self, min: float|None=None, max: float|None=None,
            change_type: str="total"):
        if min is None and max is None:
            raise ValueError(
                f"{self.__class__}: at least one of min/max is required.")
        if not min:
            min = -math.inf
        self._min = min
        if not max:
            max = math.inf
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
    def from_val(cls, val: dict):
        supported_keys = ["min",  "max", "type"]
        unsupported_keys = [k for k in val if k not in supported_keys]
        if unsupported_keys:
            raise ValueError(
                f"{cls}.from_val() got unsupported keys: {unsupported_keys}")
        return cls(
            min=val.get("min"), max=val.get("max"),
            change_type=val.get("type", "total"))

    def match(self, obj: Repository|PullRequest) -> list[MatchResult]:
        """ Returns a single match result of the form: {
            "target_type": "<what 'type' option the selector had set>"
            "min": "<the min parameter or -inf if not set>",
            "max": "<the max parameter or +inf if not set>",
            -- NOTE: the below results are only returned for PRs.
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
        # NOTE(aznashwan): Only Repositories/PRs have associated files.
        if not isinstance(obj, (PullRequest, Repository)):
            LOG.warn(
                f"{self.__class__}.match() got unsupported object type {type(obj)}: {obj}")
            return []

        res = {"min": self._min, "max": self._max}
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
        for filepath, file in lister.list_file_paths().items():
            files[filepath] = {
                "total": file.additions + file.deletions,
                "additions": file.additions,
                "deletions": file.deletions,
                "net": file.additions - file.deletions}
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
    FilesSelector,
    DiffSelector,
    TitleRegexSelector,
    DescriptionRegexSelector,
    ContributorCommentsRegexSelector,
    MaintainerCommentsRegexSelector,
]


def get_selector_cls(selector_name: str, raise_if_missing: bool=True) -> typing.Type:
    selector_name_map = {s.get_selector_name(): s for s in SELECTOR_CLASSES}
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

    match = re.search(regex, value[0:], *flags)
    if not match:
        return None

    res = {
        "full": match.string,
        "match": match.group(),
        "groups": list(match.groups()),
    }

    LOG.debug(f"Match result for {regex=} to {value=}: {res}")
    return res
