#!/usr/bin/env python3

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

""" Python utility which loads inputs based on GitHub action def in 'action.yml'. """

import json
import os
import sys

from autolabeler.main import main_with_args


def load_target_from_env() -> str|None:
    """ Attempts to load the labelling target from the os.environ by checking:
    - $INPUT_TARGET: returned directly
    - $INPUT_TARGET_FROM_ACTION_ENV: loads the target from '${{ github }}'
    """
    target = os.getenv("INPUT_TARGET")
    if target:
        return target

    github_json = os.getenv("INPUT_TARGET-FROM-ACTION-ENV")
    if not github_json:
        return None

    load = json.loads(github_json)
    repo = load.get("repository")
    if not repo:
        return None

    event = load.get("event")
    if event is None:
        return None

    pr_number = event.get("pull_request", {}).get("number", None)
    if pr_number:
        return f"{repo}/pull/{pr_number}"

    issue_number = event.get("issue", {}).get("number", None)
    if issue_number:
        return f"{repo}/issue/{issue_number}"

    return repo


def load_token_from_env() -> str|None:
    token = os.getenv("INPUT_TOKEN")
    if token:
        return token

    github_json = os.getenv("INPUT_TARGET-FROM-ACTION-ENV")
    if not github_json:
        return None
    return json.loads(github_json).get("token", None)


def load_args_from_env():
    """ Composes arguments for the labeler command from env variables.

    All of the parameters defined in 'actions.yml' should be available
    within the Docker container as uppercased "INPUT_"-prefixed env variables.
    ('example-param' -> '$INPUT_EXAMPLE_PARAM')

    https://docs.github.com/en/actions/creating-actions/metadata-syntax-for-github-actions#example-specifying-inputs
    """
    target = load_target_from_env()
    if not target:
        sys.exit(
            f"No INPUT_TARGET or INPUT_TARGET-FROM-ACTION-ENV: {os.environ}")

    vars = ["COMMAND", "CONFIG-PATH"]
    vars_map = {v: os.getenv(f"INPUT_{v}") for v in vars}
    if any(v is None for v in vars_map.values()):
        real = {f"INPUT_{v}": vars_map[v] for v in vars}
        sys.exit(
            f"One or more required env vars are undefined: {real}.\n"
            f"Env is: {os.environ}")

    token = load_token_from_env()
    if token is None:
        sys.exit(
            f"Failed to load INPUT_TOKEN or INPUT_TARGET-FROM-ACTION-ENV.token")

    return [
        "--github-token", token,
        "--label-definitions-file", vars_map['CONFIG-PATH'],
        target,
        vars_map['COMMAND']]


def main():
    args = []
    if len(sys.argv) > 1:
        args = sys.argv[1:]
    else:
        args = load_args_from_env()

    main_with_args(args)



if __name__ == "__main__":
    main()
