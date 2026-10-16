$ErrorActionPreference='Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')
$serial='emulator-5554'
$stoppedWatch = Stop-FacebookTransportWatch -Serial $serial -BasePath $Base
Write-Output ('watchdog_stopped=' + $stoppedWatch)
$targets = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -in @('emulator.exe', 'qemu-system-x86_64.exe') -and
    $_.CommandLine -and
    $_.CommandLine -match '(?i)-avd\s+fb-x86_64-api35(?:\s|$)'
})
foreach ($target in $targets) {
    Write-Output ('stopping_avd_process=' + $target.Name + ' pid=' + $target.ProcessId)
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}
$deadline=(Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    $remaining=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @('emulator.exe', 'qemu-system-x86_64.exe') -and
        $_.CommandLine -and
        $_.CommandLine -match '(?i)-avd\s+fb-x86_64-api35(?:\s|$)'
    })
} while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
Write-Output ('remaining_avd_processes=' + $remaining.Count)
if ($remaining.Count -gt 0) { throw 'Matching emulator process did not exit' }
& (Join-Path $Base 'platform-tools\adb.exe') kill-server | Out-Null
Start-Sleep -Seconds 1
& (Join-Path $Base 'platform-tools\adb.exe') start-server | Out-Null
Write-Output 'adb_server_restarted=1'
