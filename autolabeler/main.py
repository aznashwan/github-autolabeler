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
import logging
import os

import github
from github import Auth

from autolabeler import targets


def _setup_logging():
    # create logger
    logger = logging.getLogger('github-autolabeler')
    logger.setLevel(logging.DEBUG)

    # create console handler and set level to debug
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    # create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # add formatter to ch
    ch.setFormatter(formatter)

    # add ch to logger
    logger.addHandler(ch)

    return logger


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
        "-t", "--github-token", default=os.environ.get("GITHUB_TOKEN"),
        help="String GitHub API token to make labelling API calls. "
             "It can be both a 'classic' GitHub token, or a new "
             "'fine-grained' GitHub token which includes R/W access to "
             "the Issues and Pull Requests.")
    parser.add_argument(
        "-l", "--label-definitions-file", type=argparse.FileType('r'),
        help="String path to a JSON/YAML file containing label definitions.")
    parser.add_argument(
        "-r", "--replies-definitions-file", type=argparse.FileType('r'),
        help="String path to a JSON/YAML file containing issue/PR autoreply "
             "rules.")

    return parser


def _parse_target_argument(target: str) -> dict:
    """ Parses the provided target for the labelling action.

    Supports inputs of the form:
    - username/repository_name
    - user/repo/{issues/pulls} for all issues/pulls
    - user/repo/{issue/pull}/123 for a specific issue/pull

    Returns dict of the form: {
        "original": "<the original string>",
        "user": "<user/org owning the repo>",
        "repo": "<name of the repository>",
        "type": None/"issue"/"pull",
        "id": None/int,
    }
    """
    parts = target.split('/')
    if len(parts) not in range(2, 5):
        raise ValueError(
            "Target format must be a slash-separated string with the "
            "following path elements: username/repository[/type[/id]]. "
            f"Got: {target} ({len(parts)} path elements)")

    def _safe_index(n: int):
        try:
            return parts[n]
        except IndexError:
            return None

    target_type = None
    match _safe_index(2):
        case None:
            pass
        # NOTE: special concession to accept 'pulls/issues' as plurals as well.
        case 'pull' | 'pulls':
            target_type = 'pull'
        case 'issue' | 'issues':
            target_type = 'issue'
        case other:
            accepted = ['pull(s)', 'issue(s)']
            raise ValueError(
                f"Unsupported target type '{other}'. Must be one of: {accepted}")

    target_id = None
    match _safe_index(3):
        case None:
            pass
        case some:
            target_id = int(some)

    return {
        "original": target,
        "user": parts[0],
        "repo": parts[1],
        "type": target_type,
        "id": target_id,
    }


def main():
    LOG = _setup_logging()

    parser = argparse.ArgumentParser(
        "github-autolabeler",
        description="Python 3 utility for automatically labelling/triaging "
                    "GitHub issues and pull requests.")

    parser = _add_arguments(parser)

    args = parser.parse_args()

    if not args.github_token:
        raise ValueError(
            f"No GitHub API token provided via '-t/--github-token' argument "
             "or 'GITHUB_TOKEN' environment variable.")

    # TODO(aznashwan):
    # - parse config files
    # - load rules
    # - check labels generated

    LOG.info(f"{_parse_target_argument(args.target)}")

    gh = github.Github(login_or_token=args.github_token)
    # NOTE(aznashwan): required to load user:
    _ = gh.get_user().login
    gh.load(f)

    _ = targets.RepoLabelsTarget(gh, "aznashwan", "cloudbase-init")
