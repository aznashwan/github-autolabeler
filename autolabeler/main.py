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

import argparse
import json
import logging
import os
import sys
from io import IOBase

import github
import yaml

from autolabeler import manager
from autolabeler import utils


utils.setupLogging()

LOG = logging.getLogger(__name__)


def _add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    if not parser:
        raise ValueError("No parser supplied")

    parser.add_argument(
        "target",
        help="""The full 'user/repo' shorthand of the target GitHub repository.
             Optional suffixes may also be used, such as:
             Adding 'issue/123' or 'pull/456' will target a specific issues/PR.
             Adding 'issues/open' (note the plural) will target all open issues.
             Adding 'issues' or 'pulls' will target all issues/PRs.""")
    parser.add_argument(
        "command",
        choices=("generate", "sync", "purge"),
        help="Labelling command to execute. Option include: "
              "- generate: generates labels based on the config and prints them"
              "- sync: syncs labels based on the config"
              "- purge: removes all labels defined in the config from the target")
    parser.add_argument(
        "-t", "--github-token", default=os.environ.get("GITHUB_TOKEN"),
        help="String GitHub API token to make labelling API calls. "
             "It can be both a 'classic' GitHub token, or a new "
             "'fine-grained' GitHub token which includes R/W access to "
             "the Issues and Pull Requests.")
    parser.add_argument(
        "-l", "--label-definitions-file", type=argparse.FileType('r'),
        help="String path to a JSON/YAML file containing label definitions.")
    parser.add_argument(
        "-a", "--run-post-labelling-actions", action='store_true', default=False,
        help="Whether or not to run any post-labelling actions as denoted by the "
             "'action:' clauses defined on labels.")
    # parser.add_argument(
    #     "-r", "--replies-definitions-file", type=argparse.FileType('r'),
    #     help="String path to a JSON/YAML file containing issue/PR autoreply "
    #          "rules.")

    return parser


def load_yaml_file(path_or_file: str|IOBase) -> dict:
    file = path_or_file
    if isinstance(path_or_file, str):
        file = open(path_or_file, 'r')
    return yaml.safe_load(file)


def main_with_args(argv: list[str]) -> list[dict]:
    parser = argparse.ArgumentParser(
        "github-autolabeler",
        description="Python 3 utility for automatically labelling/triaging "
                    "GitHub issues and pull requests.")

    parser = _add_arguments(parser)

    args = parser.parse_args(argv)

    if not args.github_token:
        raise ValueError(
            f"No GitHub API token provided via '-t/--github-token' argument "
             "or 'GITHUB_TOKEN' environment variable.")

    gh = github.Github(login_or_token=args.github_token)
    # NOTE(aznashwan): transparent login through __getattr__:
    # NOTE^2: login API is completely inaccessible to GitHub action tokens.
    # _ = gh.get_user().login

    rules_config = {}
    if args.label_definitions_file:
        rules_config = load_yaml_file(args.label_definitions_file)

    labels = []
    label_manager = manager.LabelsManager(gh, args.target, rules_config)

    match args.command:
        case "generate":
            labels = label_manager.generate_labels()
        case "sync":
            labels = label_manager.sync_labels()
            if args.run_post_labelling_actions:
                label_manager.run_post_actions_for_labels(labels)
            else:
                # NOTE(aznashwan): if not performing an action, remove their refs.
                for l in labels:
                    l.post_labelling_action = None
                    l.post_labelling_comment = None
        case "purge":
            raise NotImplementedError("no purging yet")
        case other:
            raise ValueError(f"Unsupported command: {other}")

    labels_dicts = [l.to_dict() for l in labels]
    return labels_dicts


def main():
    labels_dicts = main_with_args(sys.argv[1:])
    print(json.dumps(labels_dicts, indent=4))
