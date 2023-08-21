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
- [Configuration](#configuration)
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
within the a repository's usual GitHub Action workflows, the utility will require
a [GitHub API Access Token](
https://docs.github.com/en/rest/guides/getting-started-with-the-rest-api?apiVersion=2022-11-28#about-tokens)
to perform the querying/labelling/commenting/closing operations it will be making.

### Personal Access Token Setup

If intending to run the utility directly and having it impersonate our GitHub user in
its automatic labelling or reply actions, we'll need to create a
[Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

It is recommended to [create a Fine-Grained Access Token](
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
with the following permissions:

Repository Permissions:
* Contents: Read-only (used for reading main Repo labels and files)
* Issues: Read and write (used to read, label, comment, and close issues)
* Pull Requests: Read and write (used to read, label, comment, and close PRs)
* Metadata: Read-only (implied by the above Issue/PR permissions)


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
    color: green
    description: This issue is a great starting point for first time contributors.
  more-eyes:
    color: yellow
    description: This issue/PR requires more maintainers to weigh in.

# This will define a label named "bug" which will automatically be associated
# with issues/PRs whose titles/descriptions match the provided regexes.
bug:
  color: red
  description: This label signifies the Issue/PR is about a bug.
  selectors:
    title: "(issue|bug|fix|problem|failure|error)"
    description: "(issue|bug|problem|failure|error)"

    # This will also make the label be applied to PRs/Issues where a maintainer
    # comments the given magic string on a single line within a comment.
    maintainer_comments: "^(/label-me-as bug)$"

# This will auto-generate a unique label for any YAML file in the 'samples' directory.
sample-{ files.name_regex.groups[0] }:  # { $SELECTOR_NAME.$FIELD_NAME.$RESULT_NAME }
  color: yellow
  description: This label was created for sample file '{ files.name_regex.full }'.
  selectors:
    files:
      name_regex: "samples/(.*).yaml"

# This will automatically label and close all PRs which contain more than 10k lines of code.
needs-splitting:
  color: red
  description: This PR has more than {diff.min} lines and must be split.
  selectors:
    author:
    diff:
       type: total  # total = additions + deletions
       min: 10000
  if: diff.min != 0
  # Will only trigger on PRs if the 'diff' selector returns a match.
  action:
    perform: close
    # NOTE: can include markdown directives in reply comment.
    comment: |
      Thank you for your contribution to our project! :smile:
      Unfortunately, this PR is *too large to be reviewed effectively*. :disappointed:
      Please break down the changes into individual PRs no larger than {diff.min} lines.
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
$ docker run ghcr.io/cloudbase/gh-auto-labeler:main -h
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
  issues:                               # triggers on issue-related operations
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
    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: "Run autolabelling"
        uses: cloudbase/gh-auto-labeler@main
        with:
          target-from-action-env: ${{ toJSON(github) }}
          # default is ".github/autolabels.yml"
          config-path: "./autolabels.yml"
          command: sync
```

## Configuration

### Basic labelers

The autolabeler has a simple YAML-based configuration syntax which is composed
from the following constructs:

Label names are string keys which map to the definitions of so-called `labelers`.

Labeler properties include:

* `color`: 6-hex-digit color for the label or one one of the pre-defined colors
  options.
* `description`: String description of the label.
* `selectors`: A mapping of pre-defined selectors which match against
               repository files, Issue/PR titles/descriptions/properties,
               and more. The matches of the selectors can be used in both
               the label's name, as well as the `description`.
* `action`: An action to take based on one or more `selector` matches.

```yaml
# This will auto-generate a label named 'my-prefix/label-for-XYZ'
# depending on selector matches.
my-prefix:
  # NOTE: selector results can be referenced in label names too
  # in this format: "{ $SELECTOR_NAME.$SELECTOR_FIELD.$SELECTOR_RESULT }"
  label-for-{ selector_1.option_1.result_1 }:
    color: 0e8a16  # green
    description: |
        This is an arbitrary label description string.
        It can also reference selector matches using:
        - { selector_1.option_1.result_1 }
        - { selector_2.option_2.result_2 if selector_1 }
    selector:
      selector_1:
        # NOTE: the "result_N" part depends on the selector's implementation.
        option_1: "<some property to match as 'selector_1.option_1.result_N'>"
      selector_2:
        option_2: "<some property to match as 'selector_2.option_2.result_N'>"
    # This will make the label only be applied if both selectors match.
    if: selector_1 and selector_2
    action:
      perform: close
      comment: |
        This PR/Issue will be closed now because both selectors returned \
        { selector_1.option_1.result_1 }.
```

<table>
<tr>
<td> Definition </td> <td> Description </td> <td> Applied on </td>
</tr>

<tr>
<td>

```yaml
example-static-label:
  color: green
  description: Example Description.
```

</td>
<td>

Defines a *static label* named `example-static-label`.

</td>
<td>

Will get created and managed at the Repo level.
Will need to be manually set by maintainers on PRs/Issues.

</td>
</tr>

<tr>
<td>

```yaml
example-prefix:
  example-sublabel-1:
    color: green
    description: Example Description 1.
  example-sublabel-1:
    color: yellow
    description: Example Description 2.
```

</td>
<td>

Defines *two namespaced static labels* named `example-prefix/example-sublabel-{1,2}`.

</td>
<td>

Will get created and managed at the Repo level.
Will need to be manually set by maintainers on PRs/Issues.

</td>
</tr>

<tr>
<td>

```yaml
example-selector-{ selector_1.option_1.result_N }:
  color: green
  description: |
    This label was automatically generated for \
    { selector_1.option_1.result_N }.
  selector:
    selector_1:
      option_1: "<some match params>"
```

</td>
<td>

Defines a *new label for every selector match* with the given format.

</td>
<td>

Will get created and managed at the Repo level IF the selector matches.
Will get automatically created and set on Issues/PRs IF the selector matches
Issues/PRs.

</td>
</tr>

<tr>
<td>

```yaml
example-action-label:
  color: green
  description: Example Description.
  selector:
    s1:
      o1: "<some match param>"
  if: s1.o1.get("result_N") is not None
  action:
    perform: |
        'close' if s1.o1.get("result_N") == 42 \
        else 'open'
    comment: |
        This PR/Issue will be \
        {'close' if s1.o1.get("result_N") == 42 \
        else 're-open'}ed now.
```

</td>
<td>

Defines a static label that will get applied to PRs/Issue and close them
IF the selector matches.

</td>
<td>

Will get created and managed at the Repo level IF the selector matches.
Will get automatically created and set on Issues/PRs IF the selector matches
Issues/PRs.
Will close PRs/Issues IF the selector returns any matches.

</td>
</tr>
</table>

### Selectors

Selectors are what determine whether a label will get applied to the given
target or not.

Please see the [Basic Labelers](#basic-labelers) section on how to use
selectors within your labelers.

#### Regular Expression-based selectors

**ALL** selectors which accept string arguments for matching (e.g. the "author"
selector which matches the user ID of a PR/Issue's author) actually accept
Regular Expression(s) as arguments in multiple forms.

```yaml
regexes-example-label:
  color: teal

  selectors:
    # An empty regex selector is an implicit catchall regex. (.*)
    author:    # equivalent with `author: null`

    # A single string is interpreted as a single regex which *must* match.
    author: "^(user123)$"  # Will only match PRs/Issues from `user123`.

    # A list of strings is interpreted as a list of regexes, *any* of which can match.
    author:
      - "^(user123)$"
      - "^(user456)$"

    # A mapping *must* contain the 'regexes' key and can define additional properties.
    author:
      strategy: all     # options are: [any]/all/none
      case_insensitive: true
      regexes: ["^(user)", "([0-9]{3})$"]

  description: |
    When a regex selector matches, it will return the following results:
    - { author.full }: the full string which matched the regex(es)
    - { author.match }: the exact substring which matched the *first* regex
    - { author.groups }: list of match groups for the *first* regex
    # NOTE: regex numbering starts from 0, i.e. `author.match0 == author.match`
    - { author.matchN }: the exact substring which matched the Nth regex
    - { author.groupsN }: list of match groups for the Nth regex
    - { author.strategy }: string containing the regex strategy (all/any/none)
    - { author.case_insensitive }: bool flag indicating case sensitivity
```

#### Author Regex Selector

The `author:` selector is a [Regular Expression-based selector](#regular-expression-based-selectors)
which matches the ID of the author of an Issue/PR.

#### Title Regex Selector

The `title:` selector is a [Regular Expression-based selector](#regular-expression-based-selectors)
which matches the title of an Issue/PR.

#### Description Regex Selector

The `description:` selector is a [Regular Expression-based selector](#regular-expression-based-selectors)
which matches the description of an Issue/PR.

#### Comments Regex Selector

The `comments:` selector is a [Regular Expression-based selector](#regular-expression-based-selectors)
which matches the comments of an Issue/PR.

Note that it returns an individual match for each comment.

#### Maintainer Comments Regex Selector

The `maintainer_comments:` selector is a [Regular Expression-based selector](#regular-expression-based-selectors)
which matches the comments let of by project mantainers on an Issue/PR.

Note that it returns an individual match for each comment.

#### Files Selector

A selector for matching file paths on Repos/PRs.

```yaml
unique-label-for-{ files.name_regex.groups[0] }:
  color: yellow
  description: |
      This label was created especially for the file whose repo path is:
      { files.name_regex.full }
  selector:
    files:
      case_insensitive: false
      name_regex: "(.*).txt"
```

Params:
* `name_regex`: regular expression to match against the full filepaths
                of all files within the Repository/Pull Request.
* `case_insensitive`: whether or not the regex should be case insensitive.

Result keys:
* `files.name_regex.full`: full file path which matched the regex.
* `files.name_regex.match`: the portion of the filename which matched the regex.
* `files.name_regex.groups[N]`: the zero-indexed Nth capture group of the regex.


#### Diff Selector

A selector for matching diff sizes for PRs.

```yaml
pr-size-measure:
  small:
    color: 0075ca
    description: This PR is between {diff.min} and {diff.max} lines of code.
    selectors:
      diff:
        # Can be: additions/deletions/total/net (net = additions - deletions)
        type: net
        min: 1
        max: 1000

  large:
    color: d73a4a
    description: This PR is over {diff.min} lines of code.
    selectors:
      diff:
        # NOTE: omitting either the min/max will set them to -/+ Infinity.
        min: 1000
```

Result keys:
* `diff.min`: the inclusive 'min' setting on the diff selector.
* `diff.max`: the NON-inclusive 'max' setting on the diff selector.
* `diff.total`: the total diff size (additions + deletions)
* `diff.addition`: the number of added lines.
* `diff.deletions`: the number of deleted lines.
* `diff.net`: the net diff (additions - deletions)
* `diff.files`: a mapping between full filenames and their diff stats:
    * `diff.files[NAME].{min,max,total,additions,deletion,net}`: individual
      diff stats for each changed file.

### Actions

[Labelers](#basic-labelers) can declare a singular `action` to be executed based
on whether one or more [selectors](#selectors) have fired or not.

```yaml
one-liner-only-file:
    color: d73a4a  # red
    description: |
        This file can only ever have PRs created which are at most {diff.min} LoC:
        { files.name_regex.full }.
    if: diff.files.get(files.name_regex.full, 0) > diff.min
    selectors:
        files:
            # will match this exact path:
            name_regex: "path/to/some/oneliner/file.txt"
        diff:
            # will match any diff with at least 1 changed line of code.
            min: 1
    action:
        perform: close
        comment: |
            Unfortunately, we do not currently accept any major contributions to
            the file: { files.name_regex.full }
            Please only change the file { diff.min } lines at a time.
```

## Advanced Usage

### Advanced configs

Please review the `./samples` folder for more advanced config examples.

### Advanced actions setup

#### Using autolabeler container directly from actions.

```yaml
# Workflow definition for automatically labelling on various triggers.
name: Autolabeler Sync
description: Workflow definition for automatically labelling on various triggers.

on:
  workflow_dispatch:                    # manual triggers from Actions menu
  push:                                 # triggers on pushes to repo branches
    branches: [main]                    # triggers on pushes to 'main'
    tags: ['v*.*.*']                    # triggers on certain tags
  issues:                               # triggers on issue-related operations
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
  autolabel-through-container:

    runs-on: ubuntu-latest
    # NOTE: replace 'main' with any specific tag you'd need.
    container: ghcr.io/cloudbase/gh-auto-labeler:main

    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: "Run autolabelling"
        run: |
          # This will check whether this action was triggered by a PR/Issue
          # event and update the target string accordingly.
          # If your `on:` section only triggers on certain types of resources,
          # you can safely hardcode the exact target format directly.
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
