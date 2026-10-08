<#
.SYNOPSIS
    Give each lab member their own Control Center login, through Caddy.

.DESCRIPTION
    The Control Center trusts only requests that come through Caddy: Caddy asks
    each person for their login (basic_auth), then adds two headers that the
    Control Center checks (see packages/labnet-control-center/src/labnet_control_center/ingress.py):

        X-LabNet-Proxy: <LABNET_CONTROL_CENTER_PROXY_SECRET>
        X-LabNet-User:  <the login name>

    This script keeps those three things in step:

    * the logins file (-UsersFile): one login name per line, '#' starts a
      comment. Names use letters, digits, '.', '_' and '-' (at most 64).
    * the Caddy snippet (-CaddySnippet): defines (labnet_control_center) with
      every login's bcrypt hash and the proxy secret. Existing logins keep
      their passwords; new logins and those named in -Reset get a new random
      password, shown ONCE at the end to hand out; logins no longer listed are
      removed.
    * the Control Center's env file (-EnvFile): the
      LABNET_CONTROL_CENTER_PROXY_SECRET line (kept if already set and usable,
      otherwise generated). Every other line is left as it is.

    Both files hold the proxy secret, so only Administrators, SYSTEM, you and
    the accounts named in -ServiceAccount (the ones Caddy and the Control
    Center run as, if not yours) may read them. A file owned by anyone else is
    refused: its owner could give themselves access again.

    -RunnerAddress and -Upstream are remembered in the snippet, so a re-run
    without them keeps the earlier values. -AnyRunnerAddress removes the
    address restriction.

    Caddy's admin API (localhost:2019) would hand the secret to any program on
    this PC; the Caddyfile must turn it off ("admin off", see
    site/Caddyfile.example). The script warns when -Caddyfile lacks it.

    Health checks (/health/*) and the Automation runner's API
    (/api/automation/runner/*, which uses runner tokens) are forwarded without a
    login, and Caddy strips the two headers from them so they can't be forged.

    Run it again whenever someone joins or leaves the lab, or forgets their
    password (-Reset name). -WhatIf shows what would change and writes nothing.

.EXAMPLE
    .\setup_logins.ps1
.EXAMPLE
    .\setup_logins.ps1 -Reset alice -RunnerAddress 192.168.50.30
.EXAMPLE
    .\setup_logins.ps1 -WhatIf
.EXAMPLE
    .\setup_logins.ps1 -ServiceAccount 'NT AUTHORITY\NetworkService'
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$UsersFile = 'C:\ProgramData\LabNet\logins.txt',
    [string]$CaddySnippet = 'C:\ProgramData\LabNet\caddy-logins.caddy',
    [string]$EnvFile = 'C:\ProgramData\LabNet\control-center.env',
    [string]$Caddy = '',
    [string[]]$Reset = @(),
    [string[]]$RunnerAddress = @(),
    [switch]$AnyRunnerAddress,
    [string]$Upstream = '',
    [string[]]$ServiceAccount = @(),
    [string]$Caddyfile = 'C:\ProgramData\LabNet\Caddyfile'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$SecretName = 'LABNET_CONTROL_CENTER_PROXY_SECRET'
$LoginPattern = '^[A-Za-z0-9._-]{1,64}$'
# bcrypt as `caddy hash-password` prints it.
$HashPattern = '^\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}$'
# Usable inside a quoted Caddyfile value: no quotes, braces (placeholders) or spaces.
$SecretPattern = '^[A-Za-z0-9._~+/=-]{32,}$'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

function Get-RandomText([string]$Alphabet, [int]$Length) {
    # Rejection sampling, so every character is equally likely.
    $limit = 256 - (256 % $Alphabet.Length)
    $builder = New-Object System.Text.StringBuilder
    $buffer = New-Object byte[] 64
    while ($builder.Length -lt $Length) {
        $Rng.GetBytes($buffer)
        foreach ($b in $buffer) {
            if ($b -lt $limit -and $builder.Length -lt $Length) {
                [void]$builder.Append($Alphabet[$b % $Alphabet.Length])
            }
        }
    }
    return $builder.ToString()
}

function New-Password {
    # 20 characters from 31 that can't be confused (no 0/o, 1/l/i), in groups of 5: ~99 bits.
    $text = Get-RandomText 'abcdefghjkmnpqrstuvwxyz23456789' 20
    return ($text.Substring(0, 5), $text.Substring(5, 5), $text.Substring(10, 5), $text.Substring(15, 5)) -join '-'
}

function Read-Logins([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ("The logins file $Path doesn't exist. Create it with one login name per line " +
               "(e.g. alice), or pass -UsersFile.")
    }
    $logins = New-Object System.Collections.Generic.List[string]
    $seen = @{}  # case-insensitive, so 'Alice' and 'alice' can't both exist
    $number = 0
    foreach ($raw in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $number++
        $line = ($raw -replace '#.*$', '').Trim()
        if (-not $line) { continue }
        if ($line -cnotmatch $LoginPattern) {
            throw ("${Path} line ${number}: '$line' is not a login name " +
                   "(letters, digits, '.', '_' and '-', at most 64).")
        }
        if ($seen.ContainsKey($line)) {
            throw "${Path} line ${number}: '$line' is listed twice (names are compared ignoring case)."
        }
        $seen[$line] = $true
        $logins.Add($line)
    }
    if ($logins.Count -eq 0) {
        throw "$Path lists no logins. Add at least one, or nobody could open the Control Center."
    }
    return , $logins.ToArray()
}

function Read-ExistingHashes([string]$Path) {
    $hashes = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return , $hashes }
    $inside = $false
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $text = $line.Trim()
        if ($text -match '^basic_auth\b.*\{$') { $inside = $true; continue }
        if ($inside -and $text -eq '}') { break }
        if ($inside -and $text -match '^(\S+)\s+(\S+)$') {
            $name = $Matches[1]
            $hash = $Matches[2]
            if ($name -cmatch $LoginPattern -and $hash -cmatch $HashPattern) { $hashes[$name] = $hash }
        }
    }
    return , $hashes
}

function Read-ExistingRouting([string]$Path) {
    # What an earlier run was given: the runner API's allowed addresses and the upstream.
    $routing = @{ RunnerAddress = @(); Upstream = '' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $routing }
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $text = $line.Trim()
        if ($text -match '^remote_ip\s+(.+)$') { $routing.RunnerAddress = @($Matches[1] -split '\s+' | Where-Object { $_ }) }
        if (-not $routing.Upstream -and $text -match '^reverse_proxy\s+(\S+)\s*\{$') { $routing.Upstream = $Matches[1] }
    }
    return $routing
}

function Find-Caddy([string]$Given) {
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given -PathType Leaf)) { throw "-Caddy: $Given doesn't exist." }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    $found = Get-Command caddy -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $found) {
        throw "Caddy isn't on PATH. Install it (caddyserver.com, 2.8 or later) or pass -Caddy C:\path\to\caddy.exe."
    }
    return $found.Source
}

function Get-PasswordHash([string]$CaddyExe, [string]$Password) {
    # On stdin, not the command line, where other programs on this PC could see it. Not
    # through a PowerShell pipe either: that ends the line with CR LF, and Caddy would
    # hash the CR as part of the password.
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $CaddyExe
    $info.Arguments = 'hash-password'
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $process.StandardInput.Write($Password + "`n")
    $process.StandardInput.Close()
    $output = $process.StandardOutput.ReadToEnd()
    [void]$process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "caddy hash-password failed (exit code $($process.ExitCode))." }
    $hash = (@($output -split "`r?`n") | ForEach-Object { "$_".Trim() } | Where-Object { $_ }) | Select-Object -Last 1
    if ("$hash" -cnotmatch $HashPattern) { throw "caddy hash-password printed something that isn't a bcrypt hash." }
    return $hash
}

function Test-RunnerAddress([string]$Text) {
    $parts = $Text.Split('/')
    $address = $null
    if ($parts.Count -gt 2 -or -not [System.Net.IPAddress]::TryParse($parts[0], [ref]$address)) { return $false }
    # .NET also takes short forms such as 192.168.50 (= 192.168.0.50); Caddy refuses them.
    if ($address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        $parts[0] -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { return $false }
    if ($parts.Count -eq 1) { return $true }
    $bits = 0
    $max = 32
    if ($address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) { $max = 128 }
    return ([int]::TryParse($parts[1], [ref]$bits) -and $bits -ge 0 -and $bits -le $max)
}

function Read-EnvLines([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "The Control Center's env file $Path doesn't exist. Pass -EnvFile, or set the Control Center up first."
    }
    $text = [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return $text
}

function Get-EnvValue([string]$Text, [string]$Name) {
    $value = $null
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match "^\s*(export\s+)?$Name\s*=(.*)$") {
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
    }
    return $value
}

function Set-EnvValue([string]$Text, [string]$Name, [string]$Value) {
    $newline = "`n"
    if ($Text.Contains("`r`n")) { $newline = "`r`n" }
    $lines = New-Object System.Collections.Generic.List[string]
    if ($Text.Length -gt 0) { $lines.AddRange([string[]]($Text -split "`r?`n")) }
    $trailing = $lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq ''
    if ($trailing) { $lines.RemoveAt($lines.Count - 1) }
    $replaced = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*(export\s+)?$Name\s*=") {
            if ($replaced) { $lines.RemoveAt($i); $i--; continue }  # one line only
            $lines[$i] = "$Name=$Value"
            $replaced = $true
        }
    }
    if (-not $replaced) {
        $lines.Add('')
        $lines.Add('# Logins: Caddy adds this to every request (scripts/lab/setup_logins.ps1).')
        $lines.Add("$Name=$Value")
    }
    return ($lines -join $newline) + $newline
}

function Get-AccountSid([string]$Account) {
    try {
        return (New-Object System.Security.Principal.NTAccount($Account)).Translate(
            [System.Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "-ServiceAccount: Windows doesn't know the account '$Account'."
    }
}

function Assert-TrustedOwner([string]$Path) {
    # Anyone may create files in C:\ProgramData; the owner of a planted file could
    # undo any restriction later, so only files we (or the admins) own are used.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $owner = (Get-Acl -LiteralPath $Path).GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if (@('S-1-5-32-544', 'S-1-5-18', $me) -notcontains $owner) {
        throw ("$Path belongs to another account ($owner), which could read or change it whatever its " +
               "permissions say. Check where it came from, delete it, and run the script again.")
    }
}

function Assert-Writable([string]$Path) {
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite)
        $stream.Dispose()
    } catch {
        throw "Can't write ${Path}: $($_.Exception.Message)"
    }
}

function Protect-File([string]$Path) {
    # Inheritance off; only Administrators, SYSTEM, the current user and -ServiceAccount
    # (by SID, so it works in any Windows language). The file holds the proxy secret.
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $grants = @('*S-1-5-32-544:(F)', '*S-1-5-18:(F)', "*${me}:(F)") + @($ServiceSids | ForEach-Object { "*${_}:(R)" })
    & icacls.exe $Path /inheritance:r /grant:r @grants | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls couldn't restrict $Path (exit code $LASTEXITCODE)." }
    # Explicit grants left from an earlier copy of the file: Users, Authenticated Users, Everyone.
    & icacls.exe $Path /remove:g '*S-1-5-32-545' '*S-1-5-11' '*S-1-1-0' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls couldn't restrict $Path (exit code $LASTEXITCODE)." }
}

function New-Snippet([string[]]$Logins, $Hashes, [string]$Secret) {
    $proxy = "`t`treverse_proxy $Upstream {"
    $runnerMatch = "`t@labnet_runner path /api/automation/runner/*"
    if ($RunnerAddress.Count -gt 0) {
        $runnerMatch = @(
            "`t@labnet_runner {",
            "`t`tpath /api/automation/runner/*",
            "`t`tremote_ip $($RunnerAddress -join ' ')",
            "`t}"
        ) -join "`n"
    }
    $users = ($Logins | ForEach-Object { "`t`t`t$_ $($Hashes[$_])" }) -join "`n"
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm')
    return @"
# The Control Center's logins, written by scripts/lab/setup_logins.ps1 on $stamp.
# Don't edit by hand: change the logins file and run the script again.
# It holds the proxy secret, so only Administrators, SYSTEM and its author may read it.
#
# Use it from the Caddyfile: "import <this file>" at the top, then
# "import labnet_control_center" inside the Control Center's site block.

(labnet_control_center) {
	# Health checks: no login. The headers are removed so they can't be forged.
	@labnet_health path /health/*
	handle @labnet_health {
$proxy
			header_up -X-LabNet-User
			header_up -X-LabNet-Proxy
		}
	}

	# The Automation runner's API: no login; runners prove themselves with a runner token.
$runnerMatch
	handle @labnet_runner {
$proxy
			header_up -X-LabNet-User
			header_up -X-LabNet-Proxy
		}
	}

	# Everything else: each person's own login.
	handle {
		basic_auth bcrypt "PQT Lab Control Center" {
$users
		}
$proxy
			header_up -Authorization
			header_up X-LabNet-Proxy "$Secret"
			header_up X-LabNet-User {http.auth.user.id}
		}
	}
}
"@ -replace "`r`n", "`n"
}

# ---- Check the inputs ------------------------------------------------------------

# Full paths: .NET's file calls don't follow PowerShell's current folder.
$UsersFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($UsersFile)
$CaddySnippet = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CaddySnippet)
$EnvFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EnvFile)
# "powershell -File" passes "a,b" as one string; accept lists either way.
$RunnerAddress = @($RunnerAddress | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_ })
$Reset = @($Reset | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_ })

$routing = Read-ExistingRouting $CaddySnippet
if ($AnyRunnerAddress) {
    if ($RunnerAddress.Count -gt 0) { throw "Use -RunnerAddress or -AnyRunnerAddress, not both." }
} elseif ($RunnerAddress.Count -eq 0 -and $routing.RunnerAddress.Count -gt 0) {
    $RunnerAddress = $routing.RunnerAddress
    Write-Host "Runner API still limited to: $($RunnerAddress -join ', ') (-AnyRunnerAddress removes that)."
}
if (-not $Upstream) { $Upstream = $routing.Upstream }
if (-not $Upstream) { $Upstream = '127.0.0.1:8080' }
$ServiceSids = @($ServiceAccount | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } |
    Where-Object { $_ } | ForEach-Object { Get-AccountSid $_ })

if ($Upstream -notmatch '^[A-Za-z0-9.\-\[\]:]+:\d{1,5}$') {
    throw "-Upstream must be host:port, e.g. 127.0.0.1:8080."
}
foreach ($address in $RunnerAddress) {
    if (-not (Test-RunnerAddress $address)) { throw "-RunnerAddress: '$address' is not an IP address or CIDR range." }
}

$logins = Read-Logins $UsersFile
foreach ($name in $Reset) {
    if (-not ($logins -ccontains $name)) { Write-Warning "-Reset: '$name' is not in $UsersFile; ignored." }
}
Assert-TrustedOwner $CaddySnippet
Assert-TrustedOwner $EnvFile
$existing = Read-ExistingHashes $CaddySnippet
$envText = Read-EnvLines $EnvFile
$caddyExe = Find-Caddy $Caddy
if (Test-Path -LiteralPath $Caddyfile -PathType Leaf) {
    if (-not (Select-String -LiteralPath $Caddyfile -Pattern '^\s*admin\s+off\b' -Quiet)) {
        Write-Warning ("$Caddyfile doesn't turn off Caddy's admin API, so any program on this PC could read " +
                       "the proxy secret from localhost:2019. Start the Caddyfile with:  { admin off }")
    }
}

# Nothing is written unless both files can be: failing halfway would leave passwords
# nobody has seen, or a secret only one side knows.
if (-not $WhatIfPreference) {
    $snippetFolder = Split-Path -Parent $CaddySnippet
    if ($snippetFolder -and -not (Test-Path -LiteralPath $snippetFolder)) {
        New-Item -ItemType Directory -Path $snippetFolder | Out-Null
    }
    Assert-Writable $EnvFile
    Assert-Writable $CaddySnippet  # creates it empty if new; restricted below before the secret goes in
}

# ---- Passwords --------------------------------------------------------------------

$hashes = @{}
$issued = New-Object System.Collections.Generic.List[object]
foreach ($name in $logins) {
    if ($existing.ContainsKey($name) -and -not ($Reset -ccontains $name)) {
        $hashes[$name] = $existing[$name]
        continue
    }
    $password = New-Password
    $hashes[$name] = Get-PasswordHash $caddyExe $password
    $reason = 'new'
    if ($existing.ContainsKey($name)) { $reason = 'reset' }
    $issued.Add([pscustomobject]@{ Login = $name; Password = $password; Why = $reason })
}
$removed = @($existing.Keys | Where-Object { -not ($logins -ccontains $_) } | Sort-Object)

# ---- The proxy secret --------------------------------------------------------------

$secret = Get-EnvValue $envText $SecretName
$secretIsNew = $false
if ($null -eq $secret -or $secret -cnotmatch $SecretPattern) {
    if ($secret) { Write-Warning "$SecretName in $EnvFile can't be used in the Caddyfile; making a new one." }
    $secret = Get-RandomText 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' 48
    $secretIsNew = $true
}

# ---- Write -------------------------------------------------------------------------
# The env file first: if the snippet then fails, a re-run reuses this secret, and no
# password has been handed out yet.

$newEnv = Set-EnvValue $envText $SecretName $secret
if ($newEnv -ceq $envText) {
    Write-Host "$SecretName in $EnvFile is already set; kept."
}
if ($PSCmdlet.ShouldProcess($EnvFile, "Restrict who can read it, and set $SecretName")) {
    Protect-File $EnvFile
    if ($newEnv -cne $envText) { [System.IO.File]::WriteAllText($EnvFile, $newEnv, $Utf8NoBom) }
}

$snippetWritten = $false
if ($PSCmdlet.ShouldProcess($CaddySnippet, "Write the Caddy logins snippet ($($logins.Count) logins)")) {
    Protect-File $CaddySnippet  # before the secret goes into it
    [System.IO.File]::WriteAllText($CaddySnippet, (New-Snippet $logins $hashes $secret), $Utf8NoBom)
    $snippetWritten = $true
}

# Shown as soon as their hashes are in place, so a later failure can't lose them.
if ($issued.Count -gt 0) {
    if ($snippetWritten) {
        Write-Host ''
        Write-Host 'New passwords. They are shown only this once: hand each to its owner now.'
        $issued | Format-Table -AutoSize Login, Password, Why | Out-String | Write-Host
    } else {
        Write-Host "Would issue new passwords for: $(($issued | ForEach-Object { $_.Login }) -join ', ') (-WhatIf: none made)."
    }
}

# ---- Report -------------------------------------------------------------------------

Write-Host ''
Write-Host "Logins: $($logins -join ', ')"
foreach ($name in $removed) { Write-Host "Removed: $name (no longer in $UsersFile)" }
if ($secretIsNew -and $snippetWritten) {
    Write-Host 'A new proxy secret was made; Caddy and the Control Center must both be restarted.'
} elseif ($secretIsNew) {
    Write-Host 'A new proxy secret would be made (-WhatIf: none made).'
}
if ($ServiceSids.Count -eq 0) {
    Write-Host ("Only Administrators, SYSTEM and $([Environment]::UserName) can read the two files. If Caddy or " +
                "the Control Center runs as another account, run again with -ServiceAccount <that account>.")
}

Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Start the Caddyfile with a block that turns off the admin API:   { admin off }'
Write-Host "     then add:   import $($CaddySnippet -replace '\\', '/')"
Write-Host '     and in the Control Center''s site block, replace its reverse_proxy line with:'
Write-Host '         import labnet_control_center'
Write-Host '     (site/Caddyfile.example shows the whole file.)'
Write-Host '  2. caddy validate --config C:\ProgramData\LabNet\Caddyfile'
Write-Host '  3. Restart Caddy, then the Control Center, so both use the same secret.'
Write-Host '  4. Check from a viewing computer: the page asks for a login, and the top bar shows "Signed in as <name>".'
