<#
Hermes tmux team - Windows installer (runs install.sh inside WSL, then adds `hermes-wsl`).

  powershell -ExecutionPolicy Bypass -File .\install.ps1                       # standalone WSL Hermes
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShareWindowsConfig   # reuse Windows Hermes settings
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Distro Ubuntu-24.04  # pick a WSL distro
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
#>
param([string]$Distro, [switch]$ShareWindowsConfig, [switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$d = @(); if ($Distro) { $d = @('-d', $Distro) }
& wsl.exe @d -e true 2>$null
if ($LASTEXITCODE -ne 0) { throw "WSL is not ready. Install it first:  wsl --install -d Ubuntu   (then reboot and open Ubuntu once)" }

$opts = @()
if ($ShareWindowsConfig) { $opts += '--share-windows-config' }
if ($Uninstall)          { $opts += '--uninstall' }
& wsl.exe @d --cd $PSScriptRoot -e bash ./install.sh @opts
if ($LASTEXITCODE -ne 0) { throw "install.sh failed (exit $LASTEXITCODE)" }

$binDir = Join-Path $HOME '.local\bin'
$cmd    = Join-Path $binDir 'hermes-wsl.cmd'
if ($Uninstall) { Remove-Item $cmd -ErrorAction SilentlyContinue; Write-Host "Removed $cmd"; return }

$wslHome = (& wsl.exe @d -e printenv HOME).Trim()
$distroArg = if ($Distro) { "-d $Distro " } else { '' }
New-Item -ItemType Directory -Force $binDir | Out-Null
Set-Content -Path $cmd -Encoding ascii -Value @(
    '@echo off'
    'rem Open Hermes in WSL (inside tmux, as team "lead") in the current folder. Extra args go to hermes.'
    "wsl.exe $distroArg--cd `"%CD%`" -e $wslHome/.local/bin/hermes-tmux %*"
)
Write-Host "Created $cmd"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $binDir) {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$binDir", 'User')
    Write-Host "Added $binDir to your PATH - open a NEW terminal before using hermes-wsl."
}
Write-Host "`nDone. Open a terminal in your project folder and run:  hermes-wsl"
