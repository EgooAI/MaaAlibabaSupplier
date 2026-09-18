param([Parameter(Mandatory=$true)][string]$ScratchParent)
$ErrorActionPreference = 'Stop'

# Load the production verification functions without process or installer I/O.
. (Join-Path $PSScriptRoot '..\app\update-helper.ps1') -ValidateOnly

function Assert-Rejected([scriptblock]$Action, [string]$Message, [string]$ErrorId) {
    $rejected = $false
    try { & $Action } catch {
        if ($_.Exception.Message -notlike $Message) { throw }
        if ($ErrorId -and $_.FullyQualifiedErrorId -cne $ErrorId) { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw "Expected rejection: $Message" }
    $script:checks++
}

if (-not (Test-Path -LiteralPath $ScratchParent -PathType Container)) { throw 'Scratch parent must exist' }
$scratch = Join-Path $ScratchParent ('maa-helper-test-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($scratch)
$checks = 0
$held = $null
try {
    $installer = Join-Path $scratch 'MaaAlibabaSupplier-v1.2.3-Setup.exe'
    $payload = [Text.Encoding]::UTF8.GetBytes('not an executable; verification fixture only')
    [IO.File]::WriteAllBytes($installer, $payload)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($payload)).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }

    # Simulate replacement after extraction but before the helper opens its lock.
    [IO.File]::WriteAllText($installer, 'replacement')
    Assert-Rejected { Open-VerifiedInstaller $installer $digest } '*Installer checksum mismatch*'
    # Rejection must release the stream so a fresh verified request can succeed.
    [IO.File]::WriteAllBytes($installer, $payload)
    $held = Open-VerifiedInstaller $installer $digest
    Assert-Rejected { [IO.File]::WriteAllText($installer, 'replacement') } '*' 'IOException'
    Assert-Rejected { [IO.File]::Delete($installer) } '*' 'IOException'
    $held.Dispose()
    $held = $null
    [IO.File]::WriteAllBytes($installer, $payload)
    $checks++

    $expected = @{
        schema_version=1; app_id='580868F7-B96A-4214-829A-609D552F2C3A'; version='v1.2.3+build.4'
        repository='EgooAI/MaaAlibabaSupplier'; sha=('b' * 40); run_id=21; run_number=13; run_attempt=2
    }
    foreach ($expectedVersion in @('v1.2.3+build.4', '1.2.3+build.4')) {
        foreach ($actualVersion in @('v1.2.3+build.4', '1.2.3+build.4')) {
            $target = $expected.Clone()
            $target.version = $expectedVersion
            $actual = $expected.Clone()
            $actual.version = $actualVersion
            Assert-InstalledBuild $actual $target
            $checks++
        }
    }
    $mismatches = @{
        schema_version=2; app_id='another-app'; repository='attacker/repo'; sha=('c' * 40)
        run_id=22; run_number=14; run_attempt=1; version='v9.9.9'
    }
    foreach ($key in $mismatches.Keys) {
        $actual = $expected.Clone()
        $actual[$key] = $mismatches[$key]
        Assert-Rejected { Assert-InstalledBuild $actual $expected } '*Installed build*mismatch*'
        $actual = $expected.Clone()
        $actual.Remove($key)
        Assert-Rejected { Assert-InstalledBuild $actual $expected } '*installed build*'
    }
    foreach ($version in @('vv1.2.3+build.4', 'V1.2.3+build.4', 'v1.2.3+BUILD.4', '', $null, 123)) {
        $actual = $expected.Clone()
        $actual.version = $version
        Assert-Rejected { Assert-InstalledBuild $actual $expected } '*Installed build version mismatch*'
    }
    foreach ($key in @('schema_version', 'run_id', 'run_number', 'run_attempt')) {
        $actual = $expected.Clone()
        $actual[$key] = [string]$actual[$key]
        Assert-Rejected { Assert-InstalledBuild $actual $expected } '*Invalid installed build identity*'
    }
    foreach ($key in @('app_id', 'repository', 'sha')) {
        $actual = $expected.Clone()
        $actual[$key] = @($actual[$key])
        Assert-Rejected { Assert-InstalledBuild $actual $expected } '*Invalid installed build identity*'
    }
    "Passed $checks nondestructive helper checks on PowerShell $($PSVersionTable.PSVersion)."
} finally {
    if ($null -ne $held) { $held.Dispose() }
    [IO.Directory]::Delete($scratch, $true)
}
