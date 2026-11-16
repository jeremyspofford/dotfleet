# Release rules

`VERSION` is the project version source. Release-please is the tool that proposes and publishes releases.
Its manifest, `.release-please-manifest.json`, is a derived version record. The driver checks equality; never edit the record independently.
`package.json` describes build tools and intentionally has no project version.

Use Conventional Commits: type-prefixed subjects such as `fix: repair setup`. A pull request (PR) proposes a repository change.
Squash change PRs with the same title: combine their commits into one commit. Subjects become plain-language changelog entries, which record release changes.
In `major.minor.patch`, the three numbers describe the release level:

| Change | Automatic result |
| --- | --- |
| `fix`, `chore`, other maintenance | Increase patch, the third number |
| `feat`, breaking changes | Increase minor, the second number |
| Major, the first number | Stay at zero indefinitely |

The workflow registers a version rule and a changelog formatter with release-please.
`bumpMinorPreMajor` routes breaking changes to minor releases. The extra guard rejects nonzero majors and every `Release-As` forced-version request before proposing.
It also checks candidate tags. There is no automatic production-ready switch.
Only the owner can declare production readiness and manually release 1.0.0. That decision requires explicit review of the automation policy.

## Baseline and operation

Dotfleet starts at 0.1.0. The bootstrap commit excludes the previous documentation-only history.
The release driver and policy tests moved from dotfiles. Content commits no longer trigger releases there.
Keep VERSION and the derived manifest equal. Release proposals remain open for owner review.
GitHub Actions runs the policy tests before proposing or publishing a release.
A dedicated RELEASE_PLEASE_TOKEN can trigger CI on generated proposals; the default GitHub token cannot trigger those extra runs.

Tests cover fixes, chores, features, breaking changes at large `0.x` minor values, forced versions, and already-production versions.
Passing unit tests does not replace the live release workflow check.
See [release-please](https://github.com/googleapis/release-please), [custom strategies](https://github.com/googleapis/release-please/blob/main/docs/customizing.md), and [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
