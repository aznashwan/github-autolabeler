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
from datetime import datetime
import enum
import itertools
import logging
import math
import re
import types
import typing
from typing import Self

from github.ContentFile import ContentFile
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Repository import Repository


LOG = logging.getLogger(__name__)

MATCH_RESULT_FIELD_REGEX = re.compile(r'^[a-zA-Z_]\w*$')

FileContainingObject = typing.Union[Repository, PullRequest]


class SelectorStrategy(enum.Enum):
    """ Possible strategies for combining selectors. """
    ALL = "all"
    ANY = "any"
    NONE = "none"

    def get_function(self):
        match self:
            case SelectorStrategy.ALL:
                return all
            case SelectorStrategy.ANY:
                return any
            case SelectorStrategy.NONE:
                return lambda it: all([not v for v in it])
            case other:
                raise ValueError(f"No action for seletor strategy {other}")


class ContributionState(enum.Enum):
    """ Possible state for issues/PRs. """
    OPEN = "open"
    CLOSED = "closed"


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
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Load the selector from a value. """
        _ = val
        _ = extra
        return NotImplemented

    @abc.abstractclassmethod
    def _get_supported_target_types(cls) -> list[type]:
        raise NotImplementedError(f"{cls}: supported object types are required.")

    @abc.abstractclassmethod
    def get_selector_name(cls):
        return NotImplemented

    @abc.abstractmethod
    def match(self, obj: object) -> list[MatchResult]:
        """ Returns a list of matches for the Github object. """
        return NotImplemented

    def _get_repo_for_object(self, obj: PullRequest|Issue) -> Repository:
        repo = None
        if isinstance(obj, Issue):
            repo = obj.repository
        else:
            as_issue = obj.as_issue()
            repo = as_issue.repository
        return repo


class MultiSelector(Selector):
    """ Aggregates match results from multiple selectors, returning
    "namespaced" match results with each sub-selector's results.
    """

    def __init__(
            self, selectors: list[Selector],
            selector_strategy: SelectorStrategy=SelectorStrategy.ANY):
        if not selectors:
            raise ValueError(
                f"{self.__class__.__name__}() requires at least one selector.")

        self._selectors = selectors
        self._selector_strategy = selector_strategy

    def __repr__(self) -> str:
        selectors = self._selectors
        selector_strategy = self._selector_strategy.value
        return f"{self.__class__.__name__}({selectors=}, {selector_strategy=})"

    @abc.abstractclassmethod
    def _get_selector_classes(cls) -> list[type]:
        raise NotImplementedError(f"{cls}: no selector classes defined.")

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        if not isinstance(val, dict):
            raise TypeError(
                f"{cls.__name__}.from_val(): requires dict, got {val}")
        if extra is None:
            extra = {}

        selectors_map = {
            s.get_selector_name(): s
            for s in cls._get_selector_classes()}  # pyright: ignore

        # TODO(aznashwan): read this from definition, or the opts?
        strategy = SelectorStrategy(
            val.pop(
                "selector_strategy",
                extra.get('selector_strategy', SelectorStrategy.ANY.value)))

        undefined_selectors = [
            sname for sname in val if sname not in selectors_map]
        if undefined_selectors:
            raise ValueError(
                f"{cls.__name__}.from_val(): unsupported selector names "
                f"{undefined_selectors}. Supported selectors are: "
                f"{list(selectors_map)}")

        selectors = []
        for selector_name, selector_def in val.items():
            selector_cls = selectors_map[selector_name]
            selectors.append(
                selector_cls.from_val(val=selector_def, extra=extra))

        return cls(selectors, selector_strategy=strategy)

    def match(self, obj: object) -> list[MatchResult]:
        supported_targets = self._get_supported_target_types()
        if not isinstance(obj, tuple(supported_targets)):
            LOG.debug(
                f"{self}.match({obj}) skipping unsupported target type "
                f"'{type(obj)}'. Supported types are {supported_targets}.")
            return []

        # TODO(aznashwan): cascade formatting options here?
        # (e.g. conditional regex params and stuff? Seems pretty redundant)
        selector_matches = {}
        for selector in self._selectors:
            selector_name = selector.get_selector_name()
            selector_matches[selector_name] = selector.match(obj)

        strategy = self._selector_strategy.get_function()
        if not strategy(selector_matches.values()):
            LOG.info(
                f"{self}.match({obj}): multi-selector strategy "
                f"{self._selector_strategy} failed on selector resuls "
                f"{selector_matches}. Returning no matches.")
            return []

        all_matches = []
        # maps index in `all_matches` to its selector name to crossref
        # results from itertools.matches.
        selector_matches_index_map = {}
        for sname, matches in selector_matches.items():
            # NOTE(aznashwan): defaulting non-matching selectors to empty dict
            # so they can be checked within statements without a nameerror.
            if not matches:
                LOG.debug(
                    f"{self}.match({obj}): selector '{sname}' returned no "
                    "matches. Defaulting to empty dict for compatibility.")
                selector_matches[sname] = [{}]
                matches = [{}]

            selector_matches_index_map[len(all_matches)] = sname
            all_matches.append(matches)

        match_results = []
        for match_set in itertools.product(*all_matches):
            match_dict = {
                selector_matches_index_map[i]: selector_match
                for i, selector_match in enumerate(match_set)}
            match_results.append(MatchResult(match_dict))

        LOG.debug(f"{self}.match({obj}) = {match_results}")
        return match_results


class BaseBooleanSelector(Selector):
    """ Abstracts boolean flag-like selectors like "is_merged". """

    def __init__(self, desired_result: bool|None=None, extra: dict|None=None):
        self._desired_result = desired_result
        self._extra = extra or {}

    @abc.abstractmethod
    def _check_criteria(self, obj: object) -> bool:
        raise NotImplementedError("Must implement criteria checking logic.")

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Load the selector from a value. """
        desired = None
        match val:
            case None:
                pass
            case True|False:
                desired = val
            case other:
                raise TypeError(
                    f"{cls.__name__}.from_dict() got unsupported definition: {other} "
                    "Must be a boolean flag or null/None/void.")

        return cls(desired_result=desired, extra=extra)

    def match(self, obj: object) -> list[MatchResult]:
        """ Returns at most a single match of the form: [{
            check: <True/False/None>
            # Same as above, provided for consistency
            match: <True/False/None>
        }]
        """
        if not isinstance(obj, tuple(self._get_supported_target_types())):
            return []

        check = self._check_criteria(obj)
        if self._desired_result != None and self._desired_result != check:
            return []

        return [MatchResult({
            "check": check,
            "match": check
        })]


class PRMergedSelector(BaseBooleanSelector):
    """ Returns at most a single match of the form: [{
        "check": True/False,
        # Same as above, provided for consistency
        match: <True/False/None>
    }]
    """
    @classmethod
    def get_selector_name(cls):
        return "merged"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _check_criteria(self, obj: PullRequest) -> bool:
        return obj.is_merged()


class PRDraftSelector(BaseBooleanSelector):
    """ Returns at most a single match of the form: [{
        "check": True/False,  # depending on whether the PR is a draft or not.
        # Same as above, provided for consistency
        match: <True/False/None>
    }]
    """
    @classmethod
    def get_selector_name(cls):
        return "draft"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _check_criteria(self, obj: PullRequest) -> bool:
        return obj.draft


class PRApprovedSelector(BaseBooleanSelector):
    """ Checks all PR reviews have been "APPROVED".
    Will NOT match PRs with no reviews.

    Returns at most a single match on PRs of the form [{
        "check": "True/False if ALL PR reviws are APPROVED"
        # Same as above, provided for consistency
        match: <True/False/None>
    }]
    """
    @classmethod
    def get_selector_name(cls):
        return "approved"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _check_criteria(self, obj: PullRequest) -> bool:
        reviews = list(obj.get_reviews())
        return bool(reviews) and all([r.state == 'APPROVED' for r in reviews])


class BaseRegexSelector(Selector):
    """ Returns a match based on a provided regexes. """

    def __init__(
            self, regexes: list[str], case_insensitive: bool=False,
            strategy: str=SelectorStrategy.ANY.value):
        self._regexes = regexes
        self._strategy = SelectorStrategy(strategy)
        self._case_insensitive = case_insensitive

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Accepts:
        None: will default to an all-capturing regex. (.*)
        str: the regex
        list: list of regexes
        dict: containing 'regexes' and 'strategy'=all/any/none

        Extra options queried:
        case_insensitive: governs regex searching case sensitivity.
        """
        # TODO(aznashwan): support explicit dict loading too.
        if not extra:
            extra = {}
        regexes = [".*"]
        strategy = SelectorStrategy.ANY.value
        case_insensitive=extra.get('case_insensitive', False)
        match val:
            case None:
                pass
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
            case val if isinstance(val, dict):
                regexes = val.get("regexes")
                if not regexes:
                    raise ValueError(
                        f"{cls.__name__}.from_val({val}): mapping definition must "
                        "contain a 'regexes' key.")
                strategy = val.get("strategy", strategy)
                case_insensitive = val.get("case_insensitive", case_insensitive)
            case other:
                raise TypeError(
                    f"{cls.__name__}.from_val({other}): unacceptable value "
                    f"of type {type(other)}: must be str or list[str].")

        return cls(regexes=regexes, strategy=strategy, case_insensitive=case_insensitive)

    def __repr__(self) -> str:
        regexes = self._regexes
        strategy = self._strategy.value
        case_insensitive = self._case_insensitive
        return f"{self.__class__.__name__}({regexes=}, {strategy=},{case_insensitive=})"

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
            "strategy": "string regex strategy",
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

        supported_types = self._get_supported_target_types()
        if not supported_types or not isinstance(
                obj, tuple(self._get_supported_target_types())):
            LOG.warn(
                f"{self.__class__}.match({obj}) got unsupported object type "
                f"{type(obj)}. Supported types are {supported_types}")
            return []

        res = []
        for item in self._get_items_to_match(obj):
            matches = {}
            string = item['string']
            for regex in self._regexes:
                match = _get_match_groups(
                    regex, string,
                    case_insensitive=self._case_insensitive)

                meta = item.get('meta', {})
                if match and meta:
                    match.update(meta)

                matches[regex] = match

            check = self._strategy.get_function()
            if not check([m is not None for m in matches.values()]):
                LOG.debug(
                    f"{self}.match({obj}): one or more regexes failed to "
                    f"satisfy strategy '{self._strategy.value}' for '{string}': "
                    f"{matches}")
                continue

            new = {
                "strategy": self._strategy.value,
                "case_insensitive": self._case_insensitive,
                "full": item}
            first = {}
            if matches.get(self._regexes[0]):
                first = matches[self._regexes[0]]
            new.update(first)

            for i, r in enumerate(matches):
                m = matches[r]
                if not m:
                    continue
                new.update({
                    f"match{i}": m["match"],
                    f"groups{i}": matches[r]["groups"]})
            res.append(new)

        return [MatchResult(m) for m in res]


class TitleRegexSelector(BaseRegexSelector):
    """ Checks the title of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "<rest>": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "title"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.title or "NOTITLE"
        }]


class ContributionStateRegexSelector(BaseRegexSelector):
    """ Checks issues/PRs have the given state (using regexes).

    Returns at most a single match of the form: [{
        "<rest>": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "state"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, (PullRequest, Issue)):
            return []
        state = obj.state
        return [{
            "string": state,
        }]



class AuthorRegexSelector(BaseRegexSelector):
    """ Checks the ID of the author of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "<rest>": "match results returned by BaseRegexSelector.match(author)..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "author"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.user.login,
        }]


class AuthorRoleRegexSelector(BaseRegexSelector):
    """ Checks the role of the author of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "<rest>": "match results returned by BaseRegexSelector.match(author)..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "author_role"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        repo = self._get_repo_for_object(obj)
        return [{
            "string": repo.get_collaborator_permission(obj.user.login),
        }]



class DescriptionRegexSelector(BaseRegexSelector):

    """ Checks the description of issues/PRs based on the given regex.

    Returns at most a single match of the form: [{
        "<rest>": "match results returned by BaseRegexSelector.match()..."
    }]
    """

    @classmethod
    def get_selector_name(cls) -> str:
        return "description"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest|Issue):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.body or "NODESCRIPTION",
        }]


class BaseCommentsRegexSelector(BaseRegexSelector):

    """ Checks the comments of issues/PRs based on the given regex and factors.

    Returns one match per comment of the form: [{
        "id": "<ID of the comment>",
        "user": "<ID of user who made the comment.>",
        "user_role": "<Role of the user on the repo>",
        "created_at": <Python datetime object when the Issue/PR was created>,
        "<rest>": "match results returned by BaseRegexSelector.match()..."
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
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
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
                "string": comm.body or "EMPTY",
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


class SourceRepoRegexPRSelector(BaseCommentsRegexSelector):
    """ Selects PRs based on regexes of the name of the source repo of the PR. """

    @classmethod
    def get_selector_name(cls) -> str:
        return "source_repo"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.head.repo.name,
        }]


class SourceBranchRegexPRSelector(BaseCommentsRegexSelector):
    """ Selects PRs based on regexes of the source branch of the PR. """

    @classmethod
    def get_selector_name(cls) -> str:
        return "source_branch"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.head.ref,
        }]


class TargetBranchRegexPRSelector(BaseCommentsRegexSelector):
    """ Selects PRs based on regexes of the target branch of the PR. """

    @classmethod
    def get_selector_name(cls) -> str:
        return "target_branch"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    def _get_items_to_match(self, obj: object) -> list[dict]:
        if not isinstance(obj, PullRequest):
            LOG.debug(
                f"{self}._get_items_to_match({obj}): invalid object param. "
                "Target object must be of type Issue or PullRequest. "
                f"Got: {type(obj)}")
            return []

        return [{
            "string": obj.base.ref,
        }]


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


class FilesSelector(Selector):
    """ Selects Repos/PRs based on contained file properties. """

    def __init__(
            self, file_name_re: str="", file_type: str="",
            name_re_case_insensitive: bool=False):
        self._file_type = file_type
        self._file_name_re = file_name_re
        self._file_re_case_insensitive = name_re_case_insensitive

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        name_regex = self._file_name_re
        name_re_case_insensitive = self._file_re_case_insensitive
        return f"{cls}({name_regex=}, {name_re_case_insensitive=})"

    @classmethod
    def get_selector_name(cls):
        return "files"

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        if not isinstance(val, dict):
            raise NotImplementedError(
                f"{cls.__name__}.from_val() requires dict, got: {val}")
        if not extra:
            extra = {}
        supported_keys = ["name_regex", "type"]
        if not any(k in val for k in supported_keys):
            raise ValueError(
                f"FilesSelector requires at least one options key "
                f"({supported_keys}). Got {val}")

        kwargs = {
            "file_name_re": val.get("name_regex", ""),
            "file_type": val.get("type", ""),
            "name_re_case_insensitive": val.get(
                "case_insensitive", extra.get("case_insensitive", False))}

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
                match = _get_match_groups(
                    self._file_name_re, path,
                    case_insensitive=self._file_re_case_insensitive)
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
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest, Issue]

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None):
        _ = extra
        if not isinstance(val, dict):
            raise NotImplementedError(
                f"{cls.__name__}.from_val() requires dict, got: {val}")

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
            "type": "<what 'type' option the selector had set>"
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

        res = {
            "min": self._min,
            "max": self._max,
            "type": self._change_type}
        if isinstance(obj, Repository):
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


class BaseLastActivitySelector(metaclass=abc.ABCMeta):
    """ Selects issues/PRs based on the duration in days since last update. """

    def __init__(self, days_since: int=0):
        self._days_since = days_since

    @classmethod
    def from_val(cls, val: object|None=None, extra: dict|None=None) -> Self:
        """ Load the selector from a value. """
        _ = extra
        if not isinstance(val, (types.NoneType, int)):
            raise ValueError(
                f"{cls}.from_val(): requires an int or None.")

        return cls(days_since=val or 0)

    @abc.abstractmethod
    def _get_last_update_timestamp(self, obj: Issue|PullRequest):
        _ = obj
        raise NotImplementedError("Must implement last timestamp.")

    def match(self, obj: object) -> list[MatchResult]:
        """ Returns a single match of the form: {
            "timestamp": "<datetime object of last update>",
            "days_since": "<int number of days since>",
            "delta": "<datetime.timedelta object representing the time diff>",
        }
        """
        if not isinstance(obj, (Issue, PullRequest)):
            LOG.debug(
                f"{self}.match({obj}) skipping unsupported target type "
                f"'{type(obj)}'. Supported types are Issues and PRs.")
            return []

        now = datetime.now()
        last_update = self._get_last_update_timestamp(obj)

        delta = now - last_update
        if self._days_since and delta.days < self._days_since:
            return []

        return [MatchResult({
            "delta": delta,
            "days_since": self._days_since,
            "timestamp": last_update,
        })]


class LastContributionActivitySelector(BaseLastActivitySelector):

    @classmethod
    def get_selector_name(cls) -> str:
        return "last_activity"

    def _get_last_update_timestamp(self, obj: Issue|PullRequest):
        return obj.updated_at or obj.created_at


class LastContributionCommentSelector(BaseLastActivitySelector):

    @classmethod
    def get_selector_name(cls) -> str:
        return "last_comment"

    def _get_last_update_timestamp(self, obj: Issue|PullRequest):

        comments = []
        if isinstance(obj, Issue):
            comments = [c for c in obj.get_comments()]
        else:
            # TODO(aznashwan): handle Review Comments too.
            as_issue = obj.as_issue()
            comments = [c for c in as_issue.get_comments()]

        last_update = obj.created_at
        for comm in comments:
            last_comm_update = comm.updated_at or comm.created_at
            if last_comm_update > last_update:
                last_update = last_comm_update

        return last_update


class RepoSelector(MultiSelector):
    @classmethod
    def get_selector_name(cls) -> str:
        return "repo"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [Repository]

    @classmethod
    def _get_selector_classes(cls) -> list[type]:
        return [FilesSelector]


class PRsSelector(MultiSelector):
    # def __init__(self,
    #              author: str|None=None,
    #              author_roles: str|None=None,
    #              target_repo: str|None=None,
    #              source_repo: str|None=None,
    #              target_branch: str|None=None,
    #              source_branch: str|None=None,
    #              is_draft: bool|None=None,
    #              is_approved: bool|None=None,
    #              title: list[str]|None=None,
    #              description: list[str]|None=None,
    #              last_activity: str|None=None):
    #     pass

    @classmethod
    def get_selector_name(cls) -> str:
        return "pr"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [PullRequest]

    @classmethod
    def _get_selector_classes(cls) -> list[type]:
        return [
            AuthorRegexSelector, AuthorRoleRegexSelector,
            TitleRegexSelector, DescriptionRegexSelector,
            ContributorCommentsRegexSelector, MaintainerCommentsRegexSelector,
            DiffSelector, FilesSelector, LastContributionActivitySelector,
            LastContributionCommentSelector, ContributionStateRegexSelector,
            PRMergedSelector, PRDraftSelector, SourceRepoRegexPRSelector,
            SourceBranchRegexPRSelector, TargetBranchRegexPRSelector,
            PRApprovedSelector]


class IssuesSelector(MultiSelector):

    @classmethod
    def get_selector_name(cls) -> str:
        return "issue"

    @classmethod
    def _get_supported_target_types(cls) -> list[type]:
        return [Issue]

    @classmethod
    def _get_selector_classes(cls) -> list[type]:
        return [
            AuthorRegexSelector, TitleRegexSelector, DescriptionRegexSelector,
            ContributorCommentsRegexSelector, MaintainerCommentsRegexSelector,
            LastContributionCommentSelector, LastContributionActivitySelector,
            ContributionStateRegexSelector]


SELECTOR_CLASSES = [
    RepoSelector,
    PRsSelector,
    IssuesSelector,
    # TODO(aznashwan): determine worth of allowing these top-level selectors
    # to be define directly instead of needing to pass through the the main
    # Repos/PRs/Issues selectors.
    AuthorRegexSelector,
    AuthorRoleRegexSelector,
    FilesSelector,
    DiffSelector,
    LastContributionActivitySelector,
    LastContributionCommentSelector,
    TitleRegexSelector,
    PRMergedSelector,
    PRDraftSelector,
    DescriptionRegexSelector,
    ContributorCommentsRegexSelector,
    MaintainerCommentsRegexSelector,
    ContributionStateRegexSelector,
    SourceRepoRegexPRSelector,
    SourceBranchRegexPRSelector,
    TargetBranchRegexPRSelector,
    PRApprovedSelector
]


def get_selector_cls(selector_name: str, raise_if_missing: bool=True) -> typing.Type:
    selector_name_map = {s.get_selector_name(): s for s in SELECTOR_CLASSES}
    if len(selector_name_map) != len(SELECTOR_CLASSES):
        selector_names = [(cls, cls.s.get_selector_name()) for cls in SELECTOR_CLASSES]
        raise ValueError(
            f"Multiple selectors share the same name: {selector_names}")

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
