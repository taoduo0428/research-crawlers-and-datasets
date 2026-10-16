$ErrorActionPreference = 'Stop'

$base = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$explorerPath = Join-Path $base 'explore_facebook_readonly.ps1'
$source = [IO.File]::ReadAllText($explorerPath)
$start = $source.IndexOf('function Get-UiNodes')
$end = $source.IndexOf('function Get-FeedSurfaceState', $start)
if ($start -lt 0 -or $end -le $start) {
    throw 'Could not isolate the UI-node and reaction-count selector functions.'
}
Invoke-Expression $source.Substring($start, $end - $start)

$reactionCn = '1' + [char]0x4e2a + [char]0x5fc3 + [char]0x60c5
$fixture = @"
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="1 comment" class="android.widget.Button" package="com.facebook.katana" clickable="true" bounds="[954,900][1080,995]" />
  <node text="" content-desc="Share, 10 shares" class="android.widget.Button" package="com.facebook.katana" clickable="true" bounds="[954,1319][1080,1481]" />
  <node text="" content-desc="$reactionCn" class="android.widget.Button" package="com.facebook.katana" clickable="true" long-clickable="false" bounds="[954,995][1080,1100]" />
  <node text="" content-desc="$reactionCn" class="android.widget.Button" package="com.facebook.katana" clickable="true" long-clickable="true" bounds="[954,1093][1080,1157]" />
  <node text="1 reaction" class="android.widget.Button" package="com.example.other" clickable="true" bounds="[954,800][1080,900]" />
</hierarchy>
"@

$tempPath = Join-Path ([IO.Path]::GetTempPath()) ("facebook-reel-selector-{0}.xml" -f ([guid]::NewGuid().ToString('N')))
try {
    [IO.File]::WriteAllText($tempPath, $fixture, [Text.UTF8Encoding]::new($false))
    $selected = Find-ReactionCountNode $tempPath
    if (-not $selected) {
        $parsed = @(Get-UiNodes $tempPath | Select-Object Label, Package, Class, Clickable, Bounds)
        throw "Reel reaction count was not selected. Parsed nodes: $($parsed | ConvertTo-Json -Compress)"
    }
    if ([string]$selected.Bounds -ne '[954,1093][1080,1157]') {
        throw "Wrong Reel reaction node selected: $($selected.Label) $($selected.Bounds)"
    }
    if (-not $selected.LongClickable) { throw 'Reel reaction-count entry must use the long-clickable node.' }
    $commentEntry = Find-CommentsDetailEntryNode $tempPath
    if (-not $commentEntry -or [string]$commentEntry.Bounds -ne '[954,900][1080,995]') {
        throw "Wrong Reel comment node selected: $($commentEntry.Label) $($commentEntry.Bounds)"
    }
    Write-Output 'PASS: strict Reel reaction selector rejected the action button and chose [954,1093][1080,1157].'
}
finally {
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}
