# Contributing

Thank you for considering contributing to this project! We appreciate all contributions, including bug reports, feature requests, documentation improvements, and code enhancements. Your feedback and involvement help make this project better for everyone.

Before submitting issues or pull requests, please review this guide to help us respond effectively to your contributions.

## Getting Started

- **Report bugs and request features** via [GitHub Issues](https://github.com/portworx/kubevirt-benchmark/issues)
- **Submit pull requests** with improvements

## How to Contribute

To open a pull request:

1. Fork the repository.
2. Modify the source; please focus on the **specific** change you are contributing.
3. Update the documentation, if required.
4. Sign-off and commit to your fork [using a clear commit message](https://cbea.ms/git-commit). Please use [Conventional Commits](https://conventionalcommits.org).
5. Open a pull request, answering any default questions in the pull request.
6. Pay attention to any automated failures reported in the pull request, and stay involved in the conversation.

GitHub provides additional documentation on [forking a repository](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/fork-a-repo) and [creating a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request).

## Contributor Flow

This is an outline of the contributor workflow:

1. Create a topic branch from where you want to base your work.
2. Make commits of logical units.
3. Make sure your commit messages are [in the proper format](https://conventionalcommits.org) and are signed-off.
4. Push your changes to the topic branch in your fork.
5. Submit a pull request. If the pull request is a work in progress, please open as draft.

> [!IMPORTANT]
> This project **requires** that commits are signed-off for the [Developer Certificate of Origin](https://probot.github.io/apps/dco/).

**Example:**

```bash
git remote add upstream https://github.com/portworx/kubevirt-benchmark.git
git checkout -b feat/add-x main
git commit --signoff --message "feat: add support for x

  Added support for x.

  Signed-off-by: Jane Doe <jdoe@example.com>

  Ref: #123"
git push origin feat/add-x
```
