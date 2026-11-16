"""Validate add-on declarations and list recorded installations. Execute no add-on code.

Internal API: validate_manifest(data, manager_version=None); installed_addons(home, manager_version=None).
A manifest declares writes, permitted reads, compatibility, and removal rules.
Use dotfleet addons --help for the user-facing command.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
"""
from pathlib import Path, PurePosixPath
import re
import tomllib

STORE = '.local/share/dotfleet/addons'
IDENTIFIER = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')
PROFILE_FIELDS = {'profile_id', 'domain', 'domain_chosen', 'git_name', 'git_email', 'pins', 'groups',
                  'agents', 'content_repo', 'secret_references'}
MACHINE_FIELDS = {'hostname', 'os', 'architecture', 'domain', 'content_repo', 'content_commit',
                  'manager_version', 'last_run', 'last_backup', 'last_run_record', 'result', 'domain_chosen', 'enrolled', 'capabilities', 'paths'}
TEARDOWN_RULES = {'owned_only', 'preserve_edits', 'preserve_credentials', 'preserve_shared_dependencies',
                  'preserve_unrelated_files', 'explain_retained', 'no_unbounded_delete'}


def require(condition, message):
    if not condition:
        raise ValueError('Add-on declaration: ' + message)


def fields(data, expected, label):
    require(isinstance(data, dict) and set(data) == expected, label + ' needs exactly: ' + ', '.join(sorted(expected)))


def version(text):
    require(isinstance(text, str) and re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', text),
            'versions need three numeric parts, such as 0.1.0')
    return tuple(map(int, text.split('.')))


def names(items, label, allowed=None):
    require(isinstance(items, list) and all(isinstance(item, str) and item for item in items), label + ' must be a list of names')
    require(len(items) == len(set(items)), label + ' contains duplicate names')
    if allowed is not None:
        require(set(items) <= allowed, label + ' contains an unsupported name')


def home_path(value):
    require(isinstance(value, str) and value and '\\' not in value and not any(ord(char) < 32 for char in value), 'paths must use home-relative names with forward slashes')
    path = PurePosixPath(value)
    require(not path.is_absolute() and value == path.as_posix() and '..' not in path.parts and value not in ('.', '~'),
            'paths cannot name home itself, an absolute path, or a parent directory')
    require(path.parts[0] not in {'dotfiles', 'dotfleet-backups', '.git'} and
            not any(path == PurePosixPath(base) or path.is_relative_to(base) for base in
                    ['.config/dotfleet', '.config/chezmoi', '.local/share/dotfleet', '.local/state/dotfleet']),
            'outputs cannot own content, backups, or manager state')
    return path


def validate_manifest(data, manager_version=None):
    """Validate schema 1 without loading profile data, reading outputs, or executing code."""
    fields(data, {'schema_version', 'id', 'version', 'description', 'compatibility', 'dependencies',
                  'install', 'reads', 'teardown'}, 'manifest')
    require(type(data['schema_version']) is int and data['schema_version'] == 1, 'unsupported schema_version')
    require(isinstance(data['id'], str) and IDENTIFIER.fullmatch(data['id']), 'id must use lowercase words joined by hyphens')
    version(data['version'])
    require(isinstance(data['description'], str) and data['description'].strip(), 'description must explain the add-on')
    compatibility = data['compatibility']
    fields(compatibility, {'manager_min', 'manager_max_exclusive', 'os', 'architectures'}, 'compatibility')
    low, high = version(compatibility['manager_min']), version(compatibility['manager_max_exclusive'])
    require(low < high, 'manager compatibility range must have increasing bounds')
    if manager_version is not None:
        require(low <= version(manager_version) < high, 'manager version is outside the declared compatibility range')
    names(compatibility['os'], 'operating systems', {'linux', 'macos', 'wsl'})
    names(compatibility['architectures'], 'architectures', {'x86_64', 'aarch64'})
    require(bool(compatibility['os']) and bool(compatibility['architectures']), 'at least one system and architecture are required')
    fields(data['dependencies'], {'tools', 'addons'}, 'dependencies')
    for key in ['tools', 'addons']:
        names(data['dependencies'][key], key + ' dependencies')
    require(all(IDENTIFIER.fullmatch(item) and item != data['id'] for item in data['dependencies']['addons']),
            'add-on dependencies need distinct add-on identifiers')
    install = data['install']
    fields(install, {'roots', 'outputs', 'tools'}, 'install')
    names(install['roots'], 'installation roots')
    roots = [home_path(path) for path in install['roots']]
    require(roots and isinstance(install['outputs'], list) and install['outputs'], 'declare installation roots and every owned output')
    seen = set()
    for output in install['outputs']:
        fields(output, {'path', 'kind'}, 'output')
        path = home_path(output['path'])
        require(path not in seen, 'each output must have one owner')
        require(any(path.is_relative_to(root) for root in roots), 'output lies outside declared installation roots')
        require(isinstance(output['kind'], str) and output['kind'] in {'configuration', 'tool', 'agent-resource'}, 'output kind must be configuration, tool, or agent-resource')
        seen.add(path)
    require(isinstance(install['tools'], dict), 'installed tools must map names to exact versions')
    for tool, selected in install['tools'].items():
        require(isinstance(tool, str) and tool and not tool.startswith('-'), 'installed tools need valid names')
        version(selected)
    fields(data['reads'], {'profile', 'machine'}, 'reads')
    names(data['reads']['profile'], 'profile reads', PROFILE_FIELDS)
    names(data['reads']['machine'], 'machine reads', MACHINE_FIELDS)
    fields(data['teardown'], TEARDOWN_RULES, 'teardown')
    require(all(value is True for value in data['teardown'].values()), 'all removal protections must be true')
    return data


def validate_receipt(receipt, manifest):
    """Require explicit opt-in and a fingerprint for every recorded owned output."""
    fields(receipt, {'schema_version', 'id', 'version', 'opted_in', 'outputs', 'tools'}, 'installation receipt')
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1, 'unsupported receipt schema_version')
    require(receipt['id'] == manifest['id'] and receipt['version'] == manifest['version'], 'receipt must match the manifest id and version')
    require(receipt['opted_in'] is True, 'receipt must record explicit profile opt-in')
    declared = {item['path'] for item in manifest['install']['outputs']}
    require(isinstance(receipt['outputs'], dict) and set(receipt['outputs']) == declared, 'receipt must identify every declared output exactly once')
    for fingerprint in receipt['outputs'].values():
        require(isinstance(fingerprint, dict), 'output fingerprint must be a table')
        if set(fingerprint) == {'link'}:
            require(isinstance(fingerprint['link'], str) and bool(fingerprint['link']), 'symlink fingerprints need a target')
        else:
            fields(fingerprint, {'sha256', 'mode'}, 'file fingerprint')
            require(isinstance(fingerprint['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', fingerprint['sha256']),
                    'file fingerprint needs a SHA-256 hash')
            require(type(fingerprint['mode']) is int and 0 <= fingerprint['mode'] <= 0o777, 'file fingerprint needs permission bits')
    require(receipt['tools'] == manifest['install']['tools'], 'receipt tools must match the declared versions')
    return receipt


def installed_addons(home, manager_version=None):
    """List validated local installation receipts. A declaration alone is not an installation."""
    root = Path(home) / STORE
    if not root.exists():
        return []
    require(root.is_dir() and not root.is_symlink(), 'add-on store must be a real directory')
    result = []
    owned = set()
    for folder in sorted(root.iterdir()):
        require(folder.is_dir() and not folder.is_symlink(), 'store entries must be real add-on directories')
        manifest_path, receipt_path = folder / 'addon.toml', folder / 'receipt.toml'
        if not receipt_path.exists():
            continue
        for path in [manifest_path, receipt_path]:
            require(path.is_file() and not path.is_symlink(), 'installed add-ons need real manifest and receipt files')
        manifest = validate_manifest(tomllib.loads(manifest_path.read_text()), manager_version)
        require(folder.name == manifest['id'], 'store directory must match the add-on id')
        receipt = validate_receipt(tomllib.loads(receipt_path.read_text()), manifest)
        paths = set(receipt['outputs'])
        require(not paths & owned, 'installed add-ons cannot own the same output')
        owned |= paths
        result.append({'id': manifest['id'], 'version': manifest['version'],
                       'description': manifest['description'], 'outputs': sorted(paths)})
    return result
