param([string]$Stage, [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
Import-Module ($PSHOME + '\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
Import-Module ($PSHOME + '\Modules\Microsoft.PowerShell.Management\Microsoft.PowerShell.Management.psd1')

# Keep this source compatible with Windows PowerShell 5.1 and its C# compiler.
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class MaaUpdateNative {
    [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetProcessTimes(IntPtr p, out long created, out long exited, out long kernel, out long user);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool QueryFullProcessImageName(IntPtr p, uint flags, StringBuilder name, ref uint length);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr OpenJobObject(uint access, bool inherit, string name);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool IsProcessInJob(IntPtr p, IntPtr job, out bool member);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle, uint ms);
    [DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();
    [StructLayout(LayoutKind.Sequential)] public struct Accounting {
        public long User, Kernel, PeriodUser, PeriodKernel;
        public uint PageFaults, Total, Active, Terminated;
    }
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool QueryInformationJobObject(IntPtr job, int info, out Accounting value, int size, IntPtr returned);
    public static bool Identity(IntPtr p, string created, string path) {
        long c, e, k, u;
        StringBuilder name = new StringBuilder(32768); uint length = 32768;
        return GetProcessTimes(p, out c, out e, out k, out u) && c.ToString() == created
            && QueryFullProcessImageName(p, 0, name, ref length)
            && String.Equals(name.ToString(), path, StringComparison.OrdinalIgnoreCase);
    }
    public static bool Member(IntPtr p, IntPtr job) {
        bool member;
        if (!IsProcessInJob(p, job, out member)) throw new Win32Exception();
        return member;
    }
    public static uint Active(IntPtr job) {
        Accounting value;
        if (!QueryInformationJobObject(job, 1, out value, Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)) throw new Win32Exception();
        return value.Active;
    }
}
'@

function Read-Json([string]$Path) {
    if ((Get-Item -LiteralPath $Path).Length -gt 65536) { throw 'Metadata too large' }
    return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
}
function Write-Json([string]$Path, $Value) {
    $temporary = $Path + '.tmp'
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 8 -Compress))
    $stream = [IO.File]::Open($temporary, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, $null) }
    else { [IO.File]::Move($temporary, $Path) }
}
function Matches-Nonce([string]$Name, [string]$Nonce) {
    $path = Join-Path $Stage $Name
    return ([IO.File]::Exists($path) -and (Read-Json $path).nonce -ceq $Nonce)
}
function Result([string]$Status, [string]$Message, $Version) {
    # Diagnostics only: a locked or missing result file must never stop the install.
    try { Write-Json $bootstrap.result @{status=$Status; message=$Message; version=$Version} } catch { }
}
function Open-VerifiedInstaller([string]$Path, [string]$ExpectedHash) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
        if ($digest -cne $ExpectedHash) { throw 'Installer checksum mismatch' }
        return $stream
    } catch { $stream.Dispose(); throw }
}
function Assert-InstalledBuild($Actual, $Expected) {
    foreach ($key in @('app_id','repository','sha')) {
        if ($Actual.$key -isnot [string]) { throw 'Invalid installed build identity' }
    }
    foreach ($key in @('schema_version','app_id','repository','sha','run_id','run_number','run_attempt')) {
        if ($Actual.$key -cne $Expected.$key) { throw 'Installed build identity mismatch' }
    }
    foreach ($key in @('schema_version','run_id','run_number','run_attempt')) {
        if ($Actual.$key -isnot [int] -and $Actual.$key -isnot [long]) { throw 'Invalid installed build identity' }
    }
    if ($Actual.version -isnot [string] -or ($Actual.version -creplace '^v', '') -cne ($Expected.version -creplace '^v', '')) {
        throw 'Installed build version mismatch'
    }
}

# Nondestructive validation loads bindings and functions before any updater I/O.
if ($ValidateOnly) { return }

# One single-use broker per installation attempt. It accepts a request, commits
# only after the application wrote go.json, verifies the installer hash, ends
# the owned job, confirms the application process exited, and installs.
$rootHandle = [IntPtr]::Zero
$jobHandle = [IntPtr]::Zero
$lockedInstaller = $null
$bootstrap = $null
$request = $null
$armed = $false
try {
    $bootstrap = Read-Json (Join-Path $Stage 'bootstrap.json')
    $rootHandle = [MaaUpdateNative]::OpenProcess(0x101000, $false, [uint32]$bootstrap.root_pid)
    if ($rootHandle -eq [IntPtr]::Zero -or -not [MaaUpdateNative]::Identity($rootHandle, $bootstrap.root_created, $bootstrap.root_path)) { throw 'Root identity mismatch' }
    $jobHandle = [MaaUpdateNative]::OpenJobObject(12, $false, $bootstrap.job)
    if ($jobHandle -eq [IntPtr]::Zero -or [MaaUpdateNative]::Member([MaaUpdateNative]::GetCurrentProcess(), $jobHandle)) { throw 'Helper containment mismatch' }

    $request = Read-Json (Join-Path $Stage 'request.json')
    $nonce = $request.nonce
    $installer = [IO.Path]::GetFullPath($request.installer)
    $base = [IO.Path]::GetFullPath((Split-Path -Parent $Stage)).TrimEnd('\') + '\'
    $install = [IO.Path]::GetFullPath($bootstrap.install).TrimEnd('\')
    if (-not $installer.StartsWith($base, [StringComparison]::OrdinalIgnoreCase) -or $installer.StartsWith($install + '\', [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($installer) -ine '.exe') { throw 'Invalid installer location' }
    if ($request.expected.app_id -cne '580868F7-B96A-4214-829A-609D552F2C3A' -or $request.expected.schema_version -ne 1) { throw 'Invalid build identity' }
    if (-not [MaaUpdateNative]::Identity($rootHandle, $bootstrap.root_created, $bootstrap.root_path) -or -not [MaaUpdateNative]::Member($rootHandle, $jobHandle)) { throw 'Root identity changed' }
    Write-Json (Join-Path $Stage 'accepted.json') @{nonce=$nonce; status='accepted'}

    # Commit only after the application finished sending its acceptance response.
    # The application may still abort before then; nothing has been stopped yet.
    $go = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([MaaUpdateNative]::WaitForSingleObject($rootHandle, 0) -ne 0) {
        if (Matches-Nonce 'cancel.json' $nonce) { break }
        if (Matches-Nonce 'go.json' $nonce) { $go = $true; break }
        if ([DateTime]::UtcNow -gt $deadline) { break }
        Start-Sleep -Milliseconds 50
    }
    if (-not $go) { return }

    # Detect replacement between download verification and this lock. The
    # retained handle prevents replacement until the installer has run.
    $lockedInstaller = Open-VerifiedInstaller $installer $request.installer_sha256
    if (-not [MaaUpdateNative]::Identity($rootHandle, $bootstrap.root_created, $bootstrap.root_path) -or -not [MaaUpdateNative]::Member($rootHandle, $jobHandle)) { throw 'Root identity changed' }
    $armed = $true
    Result 'installing' 'The updater has accepted the handoff. Installation has not yet completed.' $request.expected.version
    # One kernel operation terminates the entire non-breakaway job. No PID
    # enumeration, taskkill /T, business shutdown, or grace period.
    if (-not [MaaUpdateNative]::TerminateJobObject($jobHandle, 1)) { throw 'Process termination failed' }
    # Active()==0 is also true for an empty job. Only the root process exiting
    # proves the application actually stopped before Setup replaces its files.
    if ([MaaUpdateNative]::WaitForSingleObject($rootHandle, 10000) -ne 0) {
        Result 'error' 'The application did not stop; nothing was installed.' $request.expected.version
        return
    }
    $log = Join-Path (Split-Path -Parent $installer) 'installer.log'
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $installer
    $info.WorkingDirectory = Split-Path -Parent $installer
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.Arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /NORESTARTAPPLICATIONS /NOCLOSEAPPLICATIONS /RESTARTEXITCODE=3010 /DIR="' + $install + '" /LOG="' + $log + '"'
    $setup = [Diagnostics.Process]::Start($info)
    if (-not $setup.WaitForExit(900000)) { throw 'Installer completion timed out; no restart attempted' }
    if ($setup.ExitCode -eq 3010) { Result 'reboot_required' 'The installer requires a Windows restart. The application was not restarted.' $request.expected.version; return }
    if ($setup.ExitCode -ne 0) { throw 'Installer returned failure' }
    $actual = Read-Json (Join-Path $install 'build-info.json')
    Assert-InstalledBuild $actual $request.expected
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $install 'backend\python\pythonw.exe'
    $info.WorkingDirectory = $install
    $info.Arguments = '-m backend.app.main'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $restarted = [Diagnostics.Process]::Start($info)
    # This is process + build verification, not an HTTP health assertion.
    if ($restarted.WaitForExit(10000)) { throw 'Restarted application exited early' }
    Result 'installed' 'Installer completed and the expected build process remained running. API readiness was not verified.' $request.expected.version
    return
} catch {
    # Never serialize exception text: it can contain paths, command lines, or credentials.
    if ($armed) { Result 'error' 'Update did not complete or the expected build could not be restarted. Review the staged installer.log before retrying.' $request.expected.version }
    else { try { Write-Json (Join-Path $Stage 'accepted.json') @{nonce=$request.nonce; status='error'} } catch { } }
} finally {
    if ($null -ne $lockedInstaller) { $lockedInstaller.Dispose() }
    if ($jobHandle -ne [IntPtr]::Zero) { [void][MaaUpdateNative]::CloseHandle($jobHandle) }
    if ($rootHandle -ne [IntPtr]::Zero) { [void][MaaUpdateNative]::CloseHandle($rootHandle) }
}
