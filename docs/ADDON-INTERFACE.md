# Add-on contract

An add-on is an optional Fleet extension. Fleet is its host: the manager that controls access, installation, and removal.
A profile saves machine choices. A domain selects content additions that the content repository declares.
This document defines manifest schema 1. A manifest is a file that declares an add-on's requirements and owned outputs.

No add-on ships with Fleet. The repository contains a test fixture only.
`dotfleet addons list` lists recorded local installations. It does not install, execute, enable, or remove add-ons.
The executable lifecycle remains deferred. This contract defines its required behavior before that interface is built.

## Store and versions

Keep add-on records in `~/.fleet/addons/<id>/`.
Store the declaration as `addon.toml` and the installation receipt as `receipt.toml`.
A receipt records explicit opt-in and the outputs written by a completed installation.
A declaration without a receipt is not an installed add-on.

TOML is the structured file format used for these records. Keep profile data and receipts out of the content repository.
An ID is a unique name. Use lowercase words joined by hyphens, such as `example-tools`.
Versions contain three numeric parts, such as `0.1.0`. Schema 1 rejects floating names, version ranges, and prereleases in version fields.
Compatibility has two separate bounds. The minimum is included; the maximum is excluded.

Unknown fields fail validation. A future extension must update this contract and its schema deliberately.
Validation reads declarations and receipts only. It does not execute hooks or inspect installed output contents.

## Manifest fields

| Field | Meaning |
| --- | --- |
| `schema_version` | The integer 1. |
| `id` | Unique add-on identifier. The store folder must match it. |
| `version` | Add-on release version. |
| `description` | Plain explanation of what the add-on adds. |
| `compatibility.manager_min` | Lowest supported manager version. |
| `compatibility.manager_max_exclusive` | First unsupported manager version above the range. |
| `compatibility.os` | Supported systems: `linux`, `macos`, or `wsl`. WSL means Windows Subsystem for Linux. |
| `compatibility.architectures` | Supported processor types: `x86_64` or `aarch64`. |
| `dependencies.tools` | Names of tools the add-on needs. |
| `dependencies.addons` | IDs of required add-ons. An add-on cannot depend on itself. |
| `install.roots` | Home-relative directories where the add-on may write. |
| `install.outputs` | Every owned output, with its `path` and `kind`. |
| `install.tools` | Tool names mapped to exact versions the add-on installs. An empty table means none. |
| `reads.profile` | Explicit profile fields the add-on may receive. |
| `reads.machine` | Explicit machine fields the add-on may receive. |
| `teardown` | All removal protections listed below, each set to true. |

Output kinds are `configuration`, `tool`, and `agent-resource`. Agents are coding assistants; their resources include settings and skills.
Paths use forward slashes and stay relative to home. Absolute paths, parent traversal, and home itself are forbidden.
Outputs must stay within declared roots. They cannot own the content checkout, backups, or manager configuration and state.
The future host must also reject writes that escape these roots through existing symlinks.
Install tools, configuration, and agent resources only after the selected profile opts in.

## Data access

A capability grants specific access. An opaque secret reference identifies a secret without revealing its value.
Declarations request data; they do not grant themselves permission or read any data during validation.
The host must provide only the approved fields for the selected profile and machine.

Allowed profile fields:

- `profile_id`: the profile's identifier, when present.
- `domain`, `domain_chosen`: the domain and proof of an explicit choice.
- `git_name`, `git_email`: the recorded Git identity.
- `pins`, `groups`, `agents`: selected tool versions, optional content, and coding assistants.
- `content_repo`: the selected content repository.
- `secret_references`: opaque references only.

Allowed machine fields:

- `hostname`, `os`, `architecture`: the machine name, operating system, and processor type.
- `domain`, `content_repo`, `content_commit`, `manager_version`: recorded configuration versions and choices.
- `last_run`, `last_backup`, `last_run_record`, `result`: recorded run information.
- `domain_chosen`, `enrolled`: domain confirmation and whether the profile remains installed.
- `capabilities`, `paths`: explicitly approved permissions and paths.

Do not expose secret values, unrelated profiles, Git credentials, or arbitrary home-directory contents.
Additional access needs an explicit, documented capability and a contract update. Schema 1 rejects unlisted data fields.
An absent optional field remains absent. The host must not infer or collect extra information to fill it.

## Required lifecycle

1. Plan proposed writes and conflicts without applying changes. Record tool installations as software changes.
2. Show the standard run review and obtain profile opt-in before installation.
3. Install or update only declared outputs. Keep the result unchanged when the correct state already exists; this property is called idempotence.
4. Validate the result. Report each failure with enough detail to fix it.
5. Record ownership for every output. Two add-ons cannot silently own the same path.
6. Keep enough records after partial failure to resume safely or restore the previous state.

Git transfers source. chezmoi applies files. Mise manages tools.
Add-ons may request Layer 0 configuration through a future documented host API, or programming interface.
Layer 0 is the independent dotfiles project. Do not patch its source files or install another sync daemon, a background synchronization service.

## Receipt fields

A valid receipt has `schema_version`, `id`, `version`, `opted_in`, `outputs`, and `tools`.
The ID and version must match the declaration. `schema_version` is 1; `opted_in` must be true.
The tools table must match the declared tool versions. Every declared output must have exactly one fingerprint in `outputs`.
A fingerprint identifies the recorded output without embedding its content.

File fingerprints contain `sha256`, a 64-character lowercase content hash, and `mode`, integer permission bits from 0 through 511.
Symlink fingerprints contain `link`, the recorded target text. A symlink is a filesystem link to another path.
Receipts and manifests must be real files, not symlinks. The store and each add-on folder must be real directories.
A missing or invalid receipt cannot prove installation. An invalid installed record stops listing with a plain error.

The fixture in [tests/fixtures/addons/example](../tests/fixtures/addons/example/addon.toml) demonstrates the schema without installing anything.
Its [receipt](../tests/fixtures/addons/example/receipt.toml) supplies an example fingerprint.

## Removal protections

All seven `teardown` fields must be true:

| Field | Required behavior |
| --- | --- |
| `owned_only` | Remove only owned resources whose current state matches the recorded output. |
| `preserve_edits` | Keep user changes. |
| `preserve_credentials` | Keep credentials. |
| `preserve_shared_dependencies` | Keep tools and resources shared with other users or add-ons. |
| `preserve_unrelated_files` | Leave unrelated files unchanged. |
| `explain_retained` | Explain each retained item and how to resolve it. |
| `no_unbounded_delete` | Never recursively delete home or profile directories without explicit bounds. |

The future host must back up replaced files and include removal results in its run record.
Declaring these protections does not implement them. No executable installation or teardown hook is accepted by schema 1.
