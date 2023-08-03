# Cloudbase GitHub Auto-Labeler

Python-based utility for managing all your repo's labelling needs!

The autolabeler's main design goals were providing:

* a crystal-clear config file as a single source of truth for all label
  definitions for your Repo and its Issues and PRs.
* the ability to compose label definitions and triggers dynamically
  based on comments, PR filepaths, issue expiry, and more.
  (see advanced usecases below)
* a simple CLI interface for both manual use and easy intergration into
  existing CI/CD infrastructure.
* an optimized Docker image to allow it to run asynchronously within GitHub
  actions with no external daemon required.


## Contents

- [Prerequisites](#prerequisites)
    - [Personal Access Token Setup](#personal-access-token-setup)
    - [GitHub Action Token Setup](#github-action-token-setup)
- [Quick Setup](#quick-setup)
    - [Basic Config](#basic-config)
    - [Direct Execution](#direct-execution)
    - [GitHub Actions](#github-actions)
- [Configuration](#config)
    - [Basic Labelers](#basic-labelers)
    - [Selectors](#selectors)
        - [Files Selector](#files-selector)
        - [Comments Regex Selector](#comments-regex-selector)
        - [Diff Selector](#diff-selector)
    - [Actions](#actions)
        - [Closing Action](#closing-action)
- [Advanced Usage](#advanced-usage)


## Prerequisites

Whether used from the CLI directly by an end user, or integrated as a step
within the a repository's usual GitHub workflows, the utility will require a
[GitHub API Access Token](
https://docs.github.com/en/rest/guides/getting-started-with-the-rest-api?apiVersion=2022-11-28#about-tokens)
to perform the querying/labelling/commenting/closing operations it will be making.

### Personal Access Token Setup

If intending to run the utility directly and have it impersonate or GitHub user in
its automatic labelling or reply actions, we'll need to create a
[Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

It is recommended to [create a Fine-Grained Access Token](
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
with the following permissions:

* Repository Permissions:
    - Contents: Read-only (used for reading main Repo labels and files)
    - Issues: Read and write (used to read, label, comment, and close issues)
    - Pull Requests: Read and write (used to read, label, comment, and close PRs)
    - Metadata: Read-only (implied by the above Issue/PR permissions)


### GitHub Action Token Setup

If intending to run the utility from GitHub actions, we will need to [update
some `$GITHUB_TOKEN` settings](https://docs.github.com/en/enterprise-server@3.6/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#configuring-the-default-github_token-permissions)
on the target Repository to the "permissive setting", which will allow
individual workflows to have Read/Write access on more scopes as defined by
the per-workflow `permissions` option:

```yaml
# Our workflow files in `.github/workflows/*.yml` will need to have the following
# permissions for the utility to be able to use the `$GITHUB_TOKEN` for API calls.
permissions:
  contents: read
  issues: write
  pull-requests: write
```

## Quick Setup

### Basic Config

The config is just a YAML file which defines our label colors, descriptions, and
hirearchy, while also allowing for some more advanced label generation behavior
described in the [Advanced](#advanced-usage) section.

Here's a simple config showcasing some common patterns to start us off:

```yaml
# This will define two static "namespaced labels" which can be later be manually
# set on PRs/Issues. (help-wanted/good-first-issue and help-wanted/more-eyes)
help-wanted:
  good-first-issue:
    label-color: 0e8a16  # green
    label-description: This issue is a great starting point for first time contributors.
  more-eyes:
    label-color: e4e669  # yellow
    label-description: This issue/PR requires more maintainers to weigh in.

# This will define a label named "bug" which will automatically be associated
# with issues/PRs whose titles/descriptions match the provided regexes.
bug:
  label-color: d73a4a  # red
  label-description: This label signifies the Issue/PR is about a bug.
  selectors:
    regex:
      title: "(issue|bug|fix|problem|failure|error)"
      description: "(issue|bug|problem|failure|error)"

      # This will also make the label be applied to PRs/Issues where a maintainer
      # comments the given magic string on a single line within a comment.
      comments: "^(/label-me-as bug)$"
      maintainer-comments-only: true  # default is true anyway

# This will auto-generate a unique label for any YAML file in the 'samples' directory.
sample-{files-name-regex-group-1}:  # {$SELECTOR_NAME-$FIELD_NAME-$RESULT_NAME}
  label-color: e4e669  # yellow
  # "group-0" is the entire matching filepath.
  label-description: This label was created for sample file '{files-name-regex-group-0}'.
  selectors:
    files:
      name-regex: "samples/(.*).yaml"

# This will automatically label and close all PRs which contain more than 10k lines of code.
needs-splitting:
  label-color: d73a4a  # red
  label-description: This PR has more than {diff-min} lines and must be split.
  selectors:
    diff:
       type: total  # total = additions + deletions
       min: 10000
  action:
    # Will only trigger on PRs if the 'diff' selector returns a match.
    if: "{diff-min}"
    perform: close
    # NOTE: can include markdown directives in reply comment.
    comment: |
      Thank you for your contribution to our project! :smile:
      Unfortunately, this PR is *too large to be reviewed effectively*. :disappointed:
      Please break down the changes into individual PRs no larger than {diff-min} lines.
```

### Direct Execution

To run the executable, either:

#### Install and Run using Python/pip.

```bash
# Inside a Python dev env with pip available for installation.
# On Ubuntu, this can be set up using:
$ apt install -y git gcc python3 python3-dev python3-pip

# Clone repo and pip install its only executable script:
$ git clone https://github.com/cloudbase/gh-auto-labeler
$ cd gh-auto-labeler
$ pip3 install ./

# And call the executable, passing in any relevant arguments:
$ gh-auto-labeler \
    --github-token "$GITHUB_TOKEN" \
    --label-definitions-file "/path/to/autolabels.yml" \
    --run-post-labelling-actions \
    username/reponame[/pulls/N] \
    generate

# You can review the list of available arguments using:
$ gh-auto-labeler -h
```

#### Run using Docker

```bash
# Presuming you've copied your autolabels.yml config to /tmp so it gets mounted.
$ docker run -v /tmp:/tmp -e GITHUB_TOKEN="<your token>" \
    ghcr.io/cloudbase/gh-auto-labeler:latest \
    --label-definitions-file /tmp/autolabels.yml \
    --run-post-labelling-actions \
    username/reponame[/issues/N] \
    generate

# You can review the list of available arguments using:
$ docker run ghcr.io/cloudbase/gh-auto-labeler:latest help
```

### GitHub Actions

The autolabeler can be directly run from within a GitHub action:

```yaml
# Workflow definition for automatically labelling on various triggers.
name: Autolabeler Sync
description: Workflow definition for automatically labelling on various triggers.

on:
  workflow_dispatch:                    # manual triggers from Actions menu
  push:                                 # triggers on pushes to repo branches
    branches: [main]                    # triggers on pushes to 'main'
    tags: ['v*.*.*']                    # triggers on certain tags
  issue:                                # triggers on issue-related operations
    types: [opened, edited, closed]     # C_UD operations on the issue
  pull_request:                         # triggers on PR-related operations
    branches: [main]                    # PRs must have been opened against 'main'
    types: [opened, edited, reopened]   # triggers on PRs being (re)opened/edited
  issue_comment:                        # triggers on *both* PR/issue comments
    types: [created, edited, deleted]   # C_UD operations on any comments

permissions:
  contents: read
  issues: write
  pull-requests: write

jobs:
  autolabel:
    runs-on: ubuntu-latest
    container: ghcr.io/cloudbase/gh-auto-labeler:main
    steps:
      - name: Checkout.
        uses: actions/checkout@v3

      - name: "Run autolabelling."
        run: |
          TARGET="${{ github.repository }}"
          if [ "${{ github.event.pull_request.number }}" ]; then
            TARGET="$TARGET/pull/${{ github.event.pull_request.number }}"
          elif [ "${{ github.event.issue.number }}" ]; then
            TARGET="$TARGET/issue/${{ github.event.issue.number }}"
          fi

          gh-auto-labeler \
            --github-token ${{ secrets.GITHUB_TOKEN }} \
            --label-definitions-file "path/to/your/repos/autolabels.yml" \
            --run-post-labelling-actions \
            "$TARGET" \
            sync
```
