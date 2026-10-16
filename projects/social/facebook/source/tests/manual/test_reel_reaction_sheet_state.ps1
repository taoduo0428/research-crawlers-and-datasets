$ErrorActionPreference = 'Stop'

$base = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$source = [IO.File]::ReadAllText((Join-Path $base 'explore_facebook_readonly.ps1'))
foreach ($range in @(
    @('function Get-UiNodes', 'function Find-Node'),
    @('function Is-ReactionTabLabel', 'function Get-ReactionTabKey'),
    @('function Get-PageState', 'function Wait-ReactionListReady')
)) {
    $start = $source.IndexOf($range[0])
    $end = $source.IndexOf($range[1], $start)
    if ($start -lt 0 -or $end -le $start) { throw "Could not isolate $($range[0])." }
    Invoke-Expression $source.Substring($start, $end - $start)
}

$tabBarCn = ([char]0x5fc3) + ([char]0x60c5) + ([char]0x9009) + ([char]0x9879) + ([char]0x5361) + ([char]0x680f) + ([char]0x3002)
$allCn = ([char]0x6240) + ([char]0x6709) + ([char]0x5fc3) + ([char]0x60c5) + '1' + ([char]0x4e2a) + ' ' + ([char]0x5df2) + ([char]0x9009)
$fixtures = @(
@"
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" content-desc="$tabBarCn" class="android.view.ViewGroup" package="com.facebook.katana" clickable="false" bounds="[0,707][429,870]" />
  <node text="$allCn" content-desc="$allCn" class="android.widget.TabWidget" package="com.facebook.katana" clickable="true" bounds="[43,733][232,838]" />
  <node text="" content-desc="Er Prashant Kumar" class="android.widget.Button" package="com.facebook.katana" clickable="true" bounds="[0,1057][1080,1204]" />
</hierarchy>
"@
,
@"
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" content-desc="Reactions tab bar." class="android.view.ViewGroup" package="com.facebook.katana" clickable="false" bounds="[0,707][367,870]" />
  <node text="All reactions 1 Selected" content-desc="All reactions 1 Selected" class="android.widget.TabWidget" package="com.facebook.katana" clickable="true" bounds="[43,733][170,838]" />
  <node text="" content-desc="Fixture User" class="android.widget.Button" package="com.facebook.katana" clickable="true" bounds="[0,1051][1080,1198]" />
</hierarchy>
"@
)

$tempPath = Join-Path ([IO.Path]::GetTempPath()) ("facebook-reaction-sheet-{0}.xml" -f ([guid]::NewGuid().ToString('N')))
try {
    foreach ($fixture in $fixtures) {
        [IO.File]::WriteAllText($tempPath, $fixture, [Text.UTF8Encoding]::new($false))
        $state = Get-PageState $tempPath
        if ($state -ne 'reaction-list') { throw "Reel reaction sheet state was not recognized: $state" }
    }
    Write-Output 'PASS: Reel bottom-sheet reaction list was recognized.'
}
finally {
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}
