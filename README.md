# Dotfleet

Dotfleet will manage a fleet: a group of machines. This repository, `jeremyspofford/dotfleet`, contains documentation only.
It is Layer 1 above [dotfiles](https://github.com/jeremyspofford/dotfiles), the independent Layer 0 configuration project.
Dotfleet will use dotfiles; dotfiles will not require Dotfleet.
Git records source history. chezmoi generates home configuration files. Mise manages developer tools.
Dotfleet will coordinate these tools across machines and domains, or groups of settings.
A profile saves a machine's domain and identity.

## Planned work

- Terminal onboarding: guide setup in a text interface, called a TUI.
- Repository choice: use bundled dotfiles or the user's own repository.
- Machine profiles: select personal settings, employer settings (`work`), or client settings.
- Sync: coordinate existing tools to update machines.
- Add-ons: optional extensions with explicit install, validation, and removal rules.

No TUI, service, runtime, or add-on code exists here. The runtime is the program that would run Dotfleet.
Read the [draft add-on contract](docs/ADDON-INTERFACE.md). Follow [WRITING.md](docs/WRITING.md) when changing documentation.
Only the owner can declare production readiness and authorize version 1.0.0.
The initial tree came from the stub in `jeremyspofford/dotfiles` on 2026-11-15.
