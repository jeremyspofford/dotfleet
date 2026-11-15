# Draft add-on contract

This is a proposal. Version 0.0.1 implements no executable interface.
An add-on is an optional Dotfleet extension. Dotfleet is the host: the program that runs the add-on.
A profile holds settings for a machine and domain. Domains group personal, employer, or client settings.

## Required declarations

| Field | Meaning |
| --- | --- |
| Identifier | Unique add-on name |
| Version | Add-on release number |
| Supported systems | Operating systems and processor types, also called architectures |
| Dependencies | Tools or add-ons it needs |
| Compatibility range | Host versions it supports |
| Installation roots | Directories where it may write |
| Managed outputs | Every file or resource it owns |

Install tools, configuration, or agent resources only inside declared roots. Agents are coding assistants.
Install only after the selected profile opts in.

## Host data and access

The host may provide the profile ID, domain, operating system, processor type, capabilities, explicit paths, and opaque secret references.
An ID identifies a profile. A capability grants specific access. An opaque secret reference identifies a secret without revealing its value.
By default, expose no secret values, unrelated profiles, Git credentials, or arbitrary home-directory contents.
Additional access requires an explicit, documented capability.

## Required actions

1. Plan: report proposed writes and conflicts without applying changes.
2. Install or update: keep the result unchanged when the correct state already exists. This property is called idempotence.
3. Validate: report failures with enough detail to fix them.
4. Record ownership for each output. Two add-ons cannot silently own the same path.
5. On partial failure, keep enough output records to resume or restore the previous state safely.

## Removal

1. Remove only owned resources whose current state matches the recorded output.
2. Preserve user edits, credentials, shared dependencies, and unrelated files.
3. Explain every retained item and how to resolve it.

Never perform unbounded recursive deletion of home or profile directories.
Add-ons may request Layer 0 configuration through a documented future host API, or programming interface.
Layer 0 is the independent dotfiles project. Do not patch its source files or install another sync daemon, a background synchronization service.
Git transfers source. chezmoi applies files. Mise manages tools.
