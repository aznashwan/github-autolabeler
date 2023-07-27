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
        raise NotImplemented

    @abc.abstractmethod
    def match(self, obj: object) -> list[dict]:
        """ Returns a dict of str key-values if the Github object is matched.

        Returned keys should follow a predictable namespacing model based on the
        checked property, so for example if a selector has "check1"="<some checks>",
        the returned dict should contain: {
            "check1": "<value of match for check1>"
        }
        """
        raise NotImplemented


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

        case_insensitive: bool,
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

        if self._re_title:
            res.update(
                _get_match_groups(
                    self._re_title, obj.title, prefix="title-",
                    case_insensitive=self._case_insensitive))

        if self._re_description:
            res.update(
                _get_match_groups(
                    # NOTE: PyGithub refers to issue/PR descriptions as their "body".
                    self._re_description, obj.body, prefix="description-",
                    case_insensitive=self._case_insensitive))

        return res

    def _get_comment_matches(self, obj: Issue|PullRequest) -> dict:
        _ = obj
        if self._re_comments:
            raise NotImplemented

        # TODO(aznashwan):
        # pr.get_issue_comments/get_comments/get_review_comments() and match
        return {}

    def match(self, obj: object) -> list[dict]:
        # NOTE(aznashwan): Only Issues/PRs have the concept of comments.
        if not isinstance(obj, (Issue, PullRequest)):
            LOG.warn(
                f"RegexSelector got unsupported object type {type(obj)}: {obj}")
            return []

        res = []
        res.append(self._get_comment_matches(obj))
        res.append(self._get_title_description_matches(obj))

        return res


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

    def match(self, obj: Repository|PullRequest) -> list[dict]:
        # NOTE(aznashwan): Only Repositories/PRs have associated files.
        if not isinstance(obj, (Repository, PullRequest)):
            LOG.warn(
                f"FileSelector got unsupported object type {type(obj)}: {obj}")
            return []

        all_files = _list_files_recursively(obj, path="")
        res = []
        for file in all_files:
            match = _get_match_groups(self._file_name_re, file.path)
            if match:
                res.append(match)

        return res


SELECTORS_NAME_MAP = {
    "regex": RegexSelector,
    "files": FilesSelector,
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


def _list_files_for_object(obj: Repository|PullRequest, path: str="") -> list[ContentFile]:
    return list(obj.get_contents(path))  # pyright: ignore


def _list_files_recursively(obj: Repository|PullRequest, path="") -> list[ContentFile]:
    files = []
    for item in _list_files_for_object(obj, path=path):
        if item.type == "dir":
            files.extend(_list_files_recursively(obj, path=item.path))
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

    match = re.match(regex, value, *flags)
    if match:
        for i, group in enumerate(match.groups()):
            res[f"{prefix}group-{i}"] = group

    LOG.debug(f"Matche result for {regex=} to {value=}: {res}")

    return res
