<#
.SYNOPSIS
    Install or upgrade one LabNet role on this computer from the bundle this script is in.

.DESCRIPTION
    `labnet bundle` puts this script in every bundle. It:
      1. checks every file of the bundle against MANIFEST.json;
      2. installs labnet from the bundle into a temporary environment;
      3. runs `labnet update` from there, so the update never runs from the
         environment it replaces;
      4. removes the temporary environment.

    Run it from the bundle (a share is fine), in PowerShell:

        powershell -ExecutionPolicy Bypass -File \\server\LabNet-Releases\x.y.z\install.ps1 -Role central

    -ExecutionPolicy Bypass applies to this one run: Windows otherwise refuses
    unsigned scripts from a network share.

.PARAMETER Role
    central, control-center, lab-pc or client.

.PARAMETER LabNetHome
    The install root (default C:\ProgramData\LabNet).

.PARAMETER Python
    The python.exe that environments are made from (default: the Python
    launcher's `py -<version>` for the bundle's Python version).
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('central', 'control-center', 'lab-pc', 'client')]
    [string]$Role,
    [string]$LabNetHome = 'C:\ProgramData\LabNet',
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$bundle = $PSScriptRoot
$manifestFile = Join-Path $bundle 'MANIFEST.json'
if (-not (Test-Path $manifestFile)) {
    Write-Host "No MANIFEST.json next to this script; run the install.ps1 inside a bundle."
    exit 1
}
$manifest = Get-Content -Raw -Path $manifestFile | ConvertFrom-Json
Write-Host " ==> LabNet $($manifest.labnet_version) ($($manifest.site)) from $bundle"

# 1. Nothing is installed from a bundle with a changed or missing file.
$bad = @()
foreach ($file in $manifest.files.PSObject.Properties) {
    $path = Join-Path $bundle $file.Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $bad += "$($file.Name) is missing"
    } elseif ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.Value) {
        $bad += "$($file.Name) does not match its sha256"
    }
}
if ($bad.Count -gt 0) {
    Write-Host "This bundle is damaged or was changed; do not install it: $($bad -join '; ')"
    exit 1
}
Write-Host "   ok $(@($manifest.files.PSObject.Properties).Count) files match MANIFEST.json"

# The interpreter new environments are made from: the real one, never a venv's.
if ($Python) {
    $base = & $Python -c 'import sys; print(sys.executable)'
} else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Host "No Python launcher (py). Install Python $($manifest.python) from python.org, or pass -Python <python.exe>."
        exit 1
    }
    $base = & py "-$($manifest.python)" -c 'import sys; print(sys.executable)'
}
if ($LASTEXITCODE -ne 0 -or -not $base) {
    Write-Host "Python $($manifest.python) did not start; install it, or pass -Python <python.exe>."
    exit 1
}

# 2. labnet in a temporary environment, from the bundle's own wheels.
$tools = Join-Path $env:TEMP "labnet-install-$PID"
try {
    & $base -m venv $tools
    if ($LASTEXITCODE -ne 0) { throw "Could not create $tools." }
    $wheels = Join-Path $bundle 'wheels'
    $pipArguments = @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--find-links', $wheels)
    if ($manifest.dependencies) { $pipArguments += '--no-index' }  # everything is in the bundle
    $pipArguments += (Join-Path $wheels $manifest.packages.labnet)
    & (Join-Path $tools 'Scripts\python.exe') @pipArguments
    if ($LASTEXITCODE -ne 0) { throw 'Installing labnet from the bundle failed.' }

    # 3. The update itself.
    & (Join-Path $tools 'Scripts\labnet.exe') update --from $bundle --role $Role --home $LabNetHome --python $base
    $code = $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message
    $code = 1
} finally {
    # 4.
    if (Test-Path $tools) { Remove-Item -Recurse -Force $tools -ErrorAction SilentlyContinue }
}
exit $code
