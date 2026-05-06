# Conventional PR Title Guide

> **IMPORTANT:** Your PR title MUST follow the [Conventional Commits](https://www.conventionalcommits.org/) format (e.g., `feat(lock-client): add force release`).
> This ensures that `release-please` can automatically generate changelogs and version bumps.

## Description

A clear and concise summary of the changes. Please include the motivation and
context for this change.

---

### Related Issue

_If this PR fixes a specific issue, link it here. Use the format `Keyword #ID`
(e.g., `Fixes #1`)._

- Fixes #[issue_number]

---

### Type of Change

_Please check the type of change that this PR introduces:_

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to
      not work as expected)
- [ ] Documentation update
- [ ] Chore (e.g., refactoring, adding tests, updating dependencies)

---

### How Has This Been Tested?

Please describe the tests that you ran to verify your changes. Provide
instructions so we can reproduce.

- [ ] Manual testing performed
- [ ] Automated tests added or updated

---

### Checklist

- [ ] My code follows the style guidelines of this project
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
