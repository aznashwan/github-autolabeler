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

- [Prerequisites](#prereqs)
    - [Personal Access Token Setup](#prereqs-personal-token)
    - [GitHub Action Token Setup](#prereqs-actions-token)
- [Quick Setup](#setup)
    - [Basic Config](#quick-setup-config)
    - [Direct Execution](#quick-setup-direct)
    - [GitHub Actions](#quick-setup-action)
- [Configuration](#config)
    - [Basic Labelers](#config-labelers-basic)
    - [Selectors](#config-selectors-selectors)
        - [Files Selector](#config-selectors-files)
        - [Comments Regex Selector](#config-selectors-regex)
        - [Diff Selector](#config-selectors-diff)
    - [Actions](#config-actions)
        - [Close/Reopen Action](#config-actions-state)
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
      # Only allows maintainers to add this label via comment. (default is true anyway)
      maintainer-comments-only: true

# This is a simple label namespace: all sublabels defined within it
# will have the 'file-changes/' prefix prepended to their name.
file-changed:
  # This wil automatically define a 'sample-$NAME' sublabel for each
  # file matching the provided regex in the Repository/PR being labeled.
  sample-{files-name-regex-group-1}:  # {$SELECTOR_NAME-$FIELD_NAME-$RESULT_NAME}
    label-color: e4e669  # blue
    # NOTE: "group-0" is the entire matching filepath.
    label-description: |
      This label was created for sample file '{files-name-regex-group-0}'.
    selectors:
      files:
        name-regex: "samples/(.*).yaml"

  python-{files-name-regex-group-2}:
    label-color: 0075ca  # blue
    label-description: |
      This label was created for Python file '{files-name-regex-group-0}'.
    selectors:
      files:
        name-regex: "(.*/)+(.*).py"

# This will automatically close all PRs which contain more than 10k lines of code.
needs-splitting:
  label-color: d73a4a  # red
  label-description: |
    This PR has more than {diff-min} lines and must be broken down into smaller PRs.
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


