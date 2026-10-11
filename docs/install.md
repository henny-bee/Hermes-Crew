# Install

Supported: **Windows 10/11 with WSL2** (Ubuntu) and **Linux**. macOS is not supported yet.
See the [requirements](../README.md#requirements). Hermes itself is installed automatically into
WSL/Linux if missing.

## Windows (WSL)

1. If you don't have WSL yet: `wsl --install -d Ubuntu`, reboot, open *Ubuntu* once and create your user.
2. Copy this folder anywhere, open **PowerShell** in it and run **one** of:

```powershell
# A) New WSL Hermes with its own settings (runs Hermes setup: pick model / API key)
powershell -ExecutionPolicy Bypass -File .\install.ps1

# B) You already use Hermes on Windows: share its config, keys, skills and memories
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShareWindowsConfig
```

Options: `-Distro <name>` to target a WSL distro, `-Uninstall` to remove,
`-Models "model-a model-b"` to list the models teammates may use (different-model reviewers).

The installer installs `tmux` and Hermes inside WSL (asks for your Linux sudo password), copies
the scripts, skills and plugin, enables the plugin, and creates **`hermes-wsl`** in
`%USERPROFILE%\.local\bin` (added to your PATH). Open a **new** terminal afterwards.

## Linux / inside WSL directly

```bash
./install.sh                         # or: ./install.sh --share-windows-config  (WSL only)
                                     # add --models "model-a model-b" for different-model reviewers
source ~/.bashrc
hermes-crew doctor                   # verify the installation
```

The installer adds `tmux` via apt if it is missing.

## First run (once)

Start Hermes once and finish anything it asks (model choice, the telemetry question).
Teammates start automatically and cannot answer first-run questions.

```powershell
hermes-wsl          # Windows
```
```bash
hermes-tmux         # Linux / WSL shell
```

Then see [Usage](usage.md).

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```
Disables and removes the plugin, removes both skills, all scripts, the `~/.bashrc` lines it added and `hermes-wsl`.
Hermes itself (and any shared-config links) stay. Remove Hermes from WSL with `hermes uninstall` if you want.
`.team/` folders in your projects are left alone (delete them if you like).
