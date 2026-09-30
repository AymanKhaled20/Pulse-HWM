---
name: release
description: Prepare a release - bump __version__, turn CHANGELOG [Unreleased] into [x.y.z], verify tag/version agree. Never tags or pushes without asking.
disable-model-invocation: true
argument-hint: "[patch|minor|major|x.y.z]"
---

# Prepare a release

Argument: `$ARGUMENTS` (bump level or explicit version). If empty, propose one
from the changes using the CHANGELOG's semver rules (PATCH = fixes only,
MINOR = features/architecture, MAJOR = breaking settings/DB/sync contracts)
and confirm with the user before editing.

## Steps

1. **Preconditions** (stop and report if any fail):
   - Working tree clean (`git status --porcelain` empty).
   - Current version: `__version__` in `pulse_hwm/__init__.py`, the ONLY
     version source. Latest tag: `git tag --sort=-creatordate | Select-Object -First 1`.
   - The new version is greater than both, and tag `v<new>` does not exist.
   - `CHANGELOG.md` has a non-empty `## [Unreleased]` section.

2. **Review the changelog.** Compare `[Unreleased]` against
   `git log v<last-tag>..HEAD --oneline`. Point out user-visible commits that
   are missing and propose entries. The section is shown verbatim in the
   in-app update banner and on the GitHub release, so it must be written for
   users: no internal file names, no refactor/test-only noise.

3. **Edit** (only after the user OKs the version and entries):
   - `pulse_hwm/__init__.py`: set `__version__ = "<new>"`.
   - `CHANGELOG.md`: rename `## [Unreleased]` to `## [<new>] - <YYYY-MM-DD>`
     and add a fresh empty `## [Unreleased]` above it.
   - Do NOT touch `installer/version.iss`; CI generates it.

4. **Verify:** `pre-commit run --all-files` and `pytest` both pass. Re-read the
   new CHANGELOG section as the user would see it in the banner.

5. **Hand off.** Show the exact commands and wait for an explicit OK before
   running any of them:

   ```
   git commit -am "chore(release): v<new>"
   git tag v<new>
   git push origin <branch> v<new>
   ```

   Remind the user that pushing the tag triggers `release.yml`, which fails
   if the tag and `__version__` disagree.
