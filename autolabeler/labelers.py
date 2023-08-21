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
import dataclasses
import itertools
import logging
import traceback
from typing import Self

from github.Issue import Issue
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from autolabeler import actions
from autolabeler import expr
from autolabeler import selectors
from autolabeler import utils


LOG = logging.getLogger(__name__)

OPTIONS_MAGIC_KEY = "__opts__"
DEFINITIONS_MAGIC_KEY = "__defs__"


@dataclasses.dataclass
class LabelParams:
    name: str
    color: str
    description: str
    post_labelling_action: actions.PostLabellingAction|None = None
    post_labelling_comment: str|None = None

    def __init__(
            self, name: str, color: str, description: str,
            post_labelling_comment: str|None=None,
            post_labelling_action: actions.PostLabellingAction|None=None):
        self.name = name
        self.color = color
        self.description = description.strip()
        self.post_labelling_action = post_labelling_action
        self.post_labelling_comment = post_labelling_comment

    @classmethod
    def from_label(cls, label: Label) -> Self:
        return cls(label.name, label.color, str(label.description))

    @classmethod
    def from_dict(cls, val: dict[str, str]) -> Self:
        post_labelling_action = val.get("post_labelling_action")
        if post_labelling_action:
            post_labelling_action = actions.PostLabellingAction(
                post_labelling_action)
        return cls(val.get("name", ""),
                   val.get("color", ""),
                   val.get("description", ""),
                   post_labelling_action=post_labelling_action,  # pyright: ignore
                   post_labelling_comment=val.get("post_labelling_comment"))

    def to_dict(self) -> dict:
        res = {
            "name": self.name,
            "color": self.color,
            "description": self.description}

        action = self.post_labelling_action
        if action is not None:
            action = str(action.value)
            res["post_labelling_action"] = action
        if self.post_labelling_comment:
            res["post_labelling_comment"] = self.post_labelling_comment

        return res

    def to_label_creation_params(self) -> dict[str, str]:
        return self.to_dict()

    def __eq__(self, other: Self) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self.name == other.name and \
               self.color == other.color and \
               self.description == other.description


class BaseLabeler(metaclass=abc.ABCMeta):
    """ ABC offering independent labelling behavior for each Github resource type.
    """

    @abc.abstractmethod
    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        _ = repo
        return NotImplemented

    @abc.abstractmethod
    def get_labels_for_pr(self, pr: PullRequest) -> list[LabelParams]:
        _ = pr
        return NotImplemented

    @abc.abstractmethod
    def get_labels_for_issue(self, issue: Issue) -> list[LabelParams]:
        _ = issue
        return NotImplemented


class SelectorLabeler(BaseLabeler):
    def __init__(self,
                 label_name: str,
                 label_color: str,
                 label_description: str,
                 condition: str|None=None,
                 custom_options: dict|None=None,
                 custom_definitions: dict|None=None,
                 selectors_list: list[selectors.Selector]|None=None,
                 actioner: actions.BasePostLabellingAction|None=None):
        self._name = label_name
        self._color = utils.map_color_string(label_color)
        self._description = label_description
        self._custom_options = selectors.MatchResult(custom_options or {})
        self._custom_definitions = selectors.MatchResult(custom_definitions or {})
        self._selectors = selectors_list or []
        self._condition = condition
        self._actioner = actioner

    def __repr__(self):
        cls = self.__class__.__name__
        name = self._name
        color = self._color
        desc = self._description[:16]
        condition = self._condition
        selectors = self._selectors
        actioner = self._actioner
        return f"{cls}({name=}, {color=}, {desc=}, {condition=}, {actioner=}, {selectors=})"

    @classmethod
    def from_dict(
            cls, label_name: str, val: dict,
            custom_options: dict|None=None,
            custom_definitions: dict|None=None) -> Self:
        """ Loads SelectorLabeler from dicts of the form: {
            "color": str,        # required
            "description": str,  # required
            "if": <statement to run on selector matches>,
            "selectors": <list of raw selectors>,
            "prs": <explicit PRs selector definition>,
            "issues": <explicit Issues selector definition>,
            "repo": <explicit Repo selector definition>,
            "action": <action definition>,
        }
        """
        required_fields = [
            "color", "description"]
        if not type(val) is dict:
            raise TypeError(
                f"Expected dict with keys {required_fields}, "
                f"got {val} ({type(val)})")

        missing = [field for field in required_fields if field not in val]
        if missing:
            raise ValueError(
                f"Missing required fields {missing} in SelectorLabeler definition: {val}")

        supported_selectors = ["pr", "issue", "repo"]
        supported_fields = ["action", "if", "selectors"]
        supported_fields.extend(required_fields)
        supported_fields.extend(supported_selectors)
        unsupported = [f for f in val if f not in supported_fields]
        if unsupported:
            raise ValueError(
                f"Unsupported fields for SelectorLabeler for {label_name=}: "
                f"{unsupported}. Supported fields are: {supported_fields}")

        sels = []
        sels_defs = val.get("selectors", {})
        # Update the selectors list with first-class repo/issues/prs selectors:
        sels_defs.update({
            special: val[special]
            for special in supported_selectors
            if special in val})
        for sname, sbody in sels_defs.items():
            try:
                scls = selectors.get_selector_cls(sname, raise_if_missing=True)
                LOG.debug(
                    f"{cls.__name__}.from_dict(): attempting to load selector "
                    f"{scls.__name__} from value {val} with options {custom_options}")
                sels.append(scls.from_val(sbody, extra=custom_options))
            except Exception as ex:
                raise ValueError(
                    f"Failed to load selector '{sname}' from "
                    f"payload {sbody}:\n{ex}") from ex

        actioner = None
        actioner_def = val.get("action", {})
        if actioner_def:
            actioner = actions.OnMatchFormatAction.from_dict(actioner_def)

        return cls(
            label_name, val['color'], val['description'],
            selectors_list=sels, actioner=actioner, condition=val.get('if'),
            custom_options=custom_options,
            custom_definitions=custom_definitions)

    def _run_selectors(self, obj: Issue|PullRequest|Repository) -> dict:
        # overly-drawn-out code for logging purposes:
        matches = {}
        for selector in self._selectors:
            res = selector.match(obj)
            matches[selector.get_selector_name()] = res
            LOG.debug(f"{selector}.match({obj}) = {res}")
        return matches

    def _run_statement(self, statement: str, variables: dict) -> object:
        try:
            statement = statement.strip()
            expr.check_string_expression(statement, variables)
            return expr.evaluate_string_expression(statement, variables)
        except (NameError, SyntaxError) as ex:
            raise ex.__class__(
                f"Failed to run statement '{statement}' with {variables=}: {ex}"
            ) from ex

    def _get_labels_for_selector_matches(
            self,
            selector_matches: dict[str, list[selectors.MatchResult]]
        ) -> list[LabelParams]:
        if not self._selectors:
            # This is a static label and should be returned.
            return [LabelParams(
                self._name, self._color, self._description)]

        if self._selectors and not any(selector_matches.values()):
            # NOTE(aznashwan): if no selector matched at all, return:
            LOG.debug(f"{self} had no selector matches whatsoever, returning.")
            return []

        successful_matches = []
        selector_matches_index_map = {}  # maps index in `selector_matches` to its name
        for selector, match in selector_matches.items():
            selector_matches_index_map[len(successful_matches)] = selector
            if match:
                successful_matches.append(match)
            else:
                # NOTE(aznashwan): defaulting non-matching selectors to empty dict
                # so they can be checked within statements without a NameError.
                successful_matches.append([selectors.MatchResult({})])

        new_labels_map = {}
        for match_set in itertools.product(*successful_matches):
            # The match_dict will map selector names to their results.
            match_dict = {
                selector_matches_index_map[i]: selector_match
                for i, selector_match in enumerate(match_set)}

            # Add any custom definitions to the match result so it
            # may be accessed from within format statements:
            match_dict.update(self._custom_definitions)

            # Add any custom options in the statement:
            opts_key = 'opts'
            if opts_key in match_dict:
                LOG.error(
                    f"{self}: Skipping definitions key {opts_key} already present in "
                    f"match result set: {match_dict}")
            else:
                match_dict[opts_key] = self._custom_options

            new = None
            LOG.debug(
                f"{self}._get_labels_for_selector_matches(): attempting to format "
                f"with selectors match values: {match_dict}")
            try:
                name = expr.format_string_with_expressions(
                    self._name, match_dict)
                description = expr.format_string_with_expressions(
                    self._description, match_dict)
                if self._condition:
                    condition_result = self._run_statement(
                        self._condition, match_dict)
                    if not bool(condition_result):
                        LOG.debug(
                            f"{self}: conditional check for '{self._condition}' "
                            f"failed with {condition_result} for match set: "
                            f"{match_set}")
                        continue
                post_action = None
                post_comment = None
                if self._actioner:
                    matchres = selectors.MatchResult(match_dict)
                    post_action = self._actioner.get_post_labelling_action(matchres)
                    post_comment = self._actioner.get_post_labelling_comment(matchres)
                new = LabelParams(
                   name, self._color, description,
                    post_labelling_action=post_action,
                    post_labelling_comment=post_comment)
            except Exception as e:
                LOG.error(
                    f"{self}: skipping error formatting label params "
                    f"with match values: {match_dict}: "
                    f"{e}\n{traceback.format_exc()}")
            if not new:
                continue

            if new.name in new_labels_map and new_labels_map[new.name] != new:
                LOG.warning(
                    f"{self} got conflicting colors/descriptions for label "
                    f"{new.name}: value already present {new_labels_map.get(new.name)}"
                    f" is different from new value: {new}")

            new_labels_map[new.name] = new

        LOG.debug(
            f"{self}._get_labels_for_selector_matches(): Returning following labels "
            f"{new_labels_map} for selector matches: {selector_matches}")
        return list(new_labels_map.values())

    def get_labels_for_repo(self, repo: Repository) -> list[LabelParams]:
        # If this a simple label with a static name, it always applies to the repo.
        try:
            name = expr.format_string_with_expressions(
                self._name, self._custom_definitions)
            desc = expr.format_string_with_expressions(
                self._description, self._custom_definitions)
            return [LabelParams(name, self._color, desc)]
        except Exception as ex:
            LOG.debug(
                f"{self}.get_labels_for_repo({repo}): failed to format "
                f"label name/description. Running selectors: {ex}")
            # Else, we must run and generate the selectors:
            return self._get_labels_for_selector_matches(
                self._run_selectors(repo))

    def _get_nonstatic_labels(self, obj: PullRequest|Issue):
        # TODO(aznashwan): separate `StaticLabeler` class.
        # NOTE(aznashwan): this prevents static labellers with no selectors
        # being from applied to all PRs/Issues.
        if not self._selectors:
            LOG.warning(
                f"{self}._get_nonstatic_labels({obj}) has no selectors "
                "no non-static labels to return.")
            return []

        return self._get_labels_for_selector_matches(
            self._run_selectors(obj))

    def get_labels_for_pr(self, pr: PullRequest) -> list[LabelParams]:
        return self._get_nonstatic_labels(pr)

    def get_labels_for_issue(self, issue: Issue) -> list[LabelParams]:
        return self._get_nonstatic_labels(issue)


def load_labelers_from_config(
        config: dict, prefix: str="", separator: str="/",
        custom_options: dict|None=None,
        custom_definitions: dict|None=None,
        options_magic_key: str=OPTIONS_MAGIC_KEY,
        definitions_magic_key: str=DEFINITIONS_MAGIC_KEY) -> list[BaseLabeler]:
    """ Recursively loads labelers from the given config dict.

    The special magic keys can be repeated within each dict
    nested dict for "layering" of said config.
    """
    if not custom_definitions:
        custom_definitions = {}
    if not custom_options:
        custom_options = {}

    if not isinstance(config, dict):
        raise ValueError(
            "Failed to recursively parse config: got to the following "
            "non-mapping object in hopes it would be a labeler definition "
            f"containing a 'color' and 'description' field: {config}")

    # Evaluate and "merge" any added options in this config section.
    options = config.pop(options_magic_key, {})
    curr_options = utils.merge_dicts(custom_options, options)
    separator = curr_options.get("separator", separator)

    # Evaluate and "merge" any added definitions in this config section.
    curr_defs = custom_definitions
    custom_defs_str = config.pop(definitions_magic_key, "")
    if custom_defs_str:
        new_defs = expr.evaluate_string_definitions(
            custom_defs_str, curr_defs, scrub_imports=True,
            allowed_import_names=expr.DEFAULT_ALLOWED_IMPORTS)
        curr_defs = utils.merge_dicts(curr_defs, new_defs)

    required_labeler_keys = ['color', 'description']
    labelers = []
    for key, val in config.items():
        if not isinstance(val, dict):
            raise ValueError(
                "Failed to recursively parse config: got to the following "
                "non-mapping object in hopes it would be a labeler definition "
                f"containing a 'color' and 'description' field: {val}")


        name = key

        labeler_options_defs = val.pop(options_magic_key, {})
        labeler_options = utils.merge_dicts(custom_options, labeler_options_defs)
        separator = labeler_options.get("separator", separator)

        if prefix:
            name = f"{prefix}{separator}{key}"
        if all(k in val for k in required_labeler_keys):
            labeler_defs = {k: v for k, v in curr_defs.items()}
            labeler_defs_str = val.pop(definitions_magic_key, "")
            if labeler_defs_str:
                new_defs = expr.evaluate_string_definitions(
                    labeler_defs_str, labeler_defs, scrub_imports=True,
                    allowed_import_names=expr.DEFAULT_ALLOWED_IMPORTS)
                labeler_defs = utils.merge_dicts(labeler_defs, new_defs)

            LOG.debug(
                f"load_labelers_from_config(): attempting to define labeler "
                f"with name '{name}' with payload {val} and custom defs: "
                f"{labeler_defs}")
            labelers.append(
                SelectorLabeler.from_dict(
                    name, val,
                    custom_options=labeler_options,
                    custom_definitions=labeler_defs))
        else:
            labelers.extend(load_labelers_from_config(
                val, prefix=name, separator=separator,
                custom_options=curr_options,
                custom_definitions=curr_defs))

    return labelers
