param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RecycleRoot = Join-Path $ProjectRoot "rollback-artifacts\boss-public-card-rollback-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$DetailDir = Join-Path $ProjectRoot ".crawler-state\recruitment-details\BOSS"
$MergedArtifact = Get-ChildItem -LiteralPath $DetailDir -File | Where-Object { $_.Length -gt 20000 } | Sort-Object Length -Descending | Select-Object -First 1
$Targets = @(
    (Join-Path $ProjectRoot "tools\backfill_boss_public_card_jd.py"),
    (Join-Path $ProjectRoot "tests\test_boss_public_card_backfill.py"),
    (Join-Path $ProjectRoot ".crawler-state\boss-public-card"),
    $MergedArtifact.FullName
)

foreach ($Target in $Targets) {
    if (-not (Test-Path -LiteralPath $Target)) {
        continue
    }
    $Resolved = [IO.Path]::GetFullPath($Target)
    if (-not $Resolved.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target is outside project root: $Resolved"
    }
    $Relative = $Resolved.Substring($ProjectRoot.Length).TrimStart('\', '/')
    $Destination = Join-Path $RecycleRoot $Relative
    $DestinationParent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $DestinationParent | Out-Null
    Move-Item -LiteralPath $Resolved -Destination $Destination -WhatIf:$WhatIf
    Write-Output ("{0} -> {1}" -f $Resolved, $Destination)
}

if (-not $WhatIf) {
    Write-Output ("Rollback artifacts moved to: {0}" -f $RecycleRoot)
}
