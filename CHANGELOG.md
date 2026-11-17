# Changelog

This file follows Keep a Changelog. Versions use SemVer: major.minor.patch.

## [0.4.0] - 2026-11-17

### Added

- add Fleet terminal interface

## [0.3.0] - 2026-11-17

### Added

- rename Dotfleet to Fleet and migrate manager stores (#22)
- check manager and content updates against their upstreams (#21)
- share logger styling across command output
- add human commands for owned stores
- add recorded fleet registry and finalize add-on contracts (#14)
- add recorded fleet registry and finalize add-on contracts
- record machines in the manager fleet registry
- define the manager add-on contract and decisions
- add dotfleet commands for enrolled machines (#11)
- add dotfleet commands for enrolled machines
- render readable run results from one record (#10)
- render readable run results from one record
- report planned and completed run changes (#8)
- report planned and completed run changes

### Changed

- apply code standards without changing command behavior
- route profile changes through commands
- integrate shared output styling
- integrate owned-store commands
- integrate read-only diff fix
- define registry and add-on terms in command help
- integrate fleet command provenance
- integrate readable run output
- preserve fleet records across rollback and uninstall
- record machine home paths in the registry schema

### Fixed

- show Fleet and available command names
- use logger section headers in help and prompt checks
- use per-agent toggles and detect installed assistants
- summarize wizard scans and keep source defaults consistent
- keep scan and agent imports together
- reuse scanned tools for agent detection
- show read-only file patches in diff
- wrap command help and keep one usage prefix
- keep registry and profile ownership in the manager
- make legacy domain provenance explicit (#13)
- make legacy domain provenance explicit
- compact unchanged runs and bound review output (#12)
- compact unchanged runs and bound review output
- explain unchosen domains in fleet commands
- preserve changed external targets before rollback
- require a terminal to reopen the wizard

## [0.2.0] - 2026-11-16

### Added

- add reviewed enrollment wizard (#7)
- add reviewed enrollment wizard
- add Dotfleet manager and plain content conversion

### Changed

- correct manager inventory and release review guidance
- verify interactive login shells during enrollment

### Fixed

- clarify domain additions and format terminal prompts (#6)
- clarify domain additions and format terminal prompts
- explain domain choices and saved defaults
- validate enrollment profiles and reconcile chezmoi config state
- preserve migrated Mise settings and test merged content

## [0.1.0]

### Added

- Manage plain dotfiles, machine profiles, backups, and generated settings.
