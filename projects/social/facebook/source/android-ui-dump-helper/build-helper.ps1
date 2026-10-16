[CmdletBinding()]
param([string]$SdkRoot = 'D:\Android\Sdk', [string]$BuildToolsVersion = '35.0.0')
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$AndroidJar = Join-Path $SdkRoot 'platforms\android-35\android.jar'
$BuildTools = Join-Path $SdkRoot ("build-tools\{0}" -f $BuildToolsVersion)
$Aapt = Join-Path $BuildTools 'aapt.exe'
$Zipalign = Join-Path $BuildTools 'zipalign.exe'
$ApkSigner = Join-Path $BuildTools 'apksigner.bat'
$D8 = Join-Path $SdkRoot 'cmdline-tools\latest\bin\d8.bat'
if (-not (Test-Path -LiteralPath $D8)) { $D8 = Join-Path $BuildTools 'd8.bat' }
$Javac = (Get-Command javac -ErrorAction Stop).Source
$DebugKeystore = Join-Path $env:USERPROFILE '.android\debug.keystore'
foreach ($required in @($AndroidJar,$Aapt,$Zipalign,$ApkSigner,$D8,$DebugKeystore)) { if (-not (Test-Path $required)) { throw "Missing Android build dependency: $required" } }
$Build = Join-Path $Base 'build\repro'; $Classes = Join-Path $Build 'classes'; $Dex = Join-Path $Build 'dex'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Build
New-Item -ItemType Directory -Force -Path $Classes,$Dex | Out-Null
$Source = Join-Path $Base 'src\com\openai\facebookuidump\DumpInstrumentation.java'
& $Javac -source 8 -target 8 -encoding UTF-8 -bootclasspath $AndroidJar -d $Classes $Source
if ($LASTEXITCODE -ne 0) { throw "javac failed: $LASTEXITCODE" }
& $D8 --lib $AndroidJar --output $Dex (Join-Path $Classes 'com\openai\facebookuidump\DumpInstrumentation.class') (Join-Path $Classes 'com\openai\facebookuidump\DumpInstrumentation$ScrollResult.class')
if ($LASTEXITCODE -ne 0) { throw "d8 failed: $LASTEXITCODE" }
$Unsigned = Join-Path $Build 'helper-unsigned.apk'; $Aligned = Join-Path $Build 'helper-aligned.apk'; $Final = Join-Path $Base 'facebook-ui-dump-helper.apk'
& $Aapt package -f -M (Join-Path $Base 'AndroidManifest.xml') -I $AndroidJar -F $Unsigned
if ($LASTEXITCODE -ne 0) { throw "aapt package failed: $LASTEXITCODE" }
Push-Location $Dex
try { & $Aapt add $Unsigned 'classes.dex' } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "aapt add classes.dex failed: $LASTEXITCODE" }
& $Zipalign -f 4 $Unsigned $Aligned
if ($LASTEXITCODE -ne 0) { throw "zipalign failed: $LASTEXITCODE" }
& $ApkSigner sign --ks $DebugKeystore --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out $Final $Aligned
if ($LASTEXITCODE -ne 0) { throw "apksigner failed: $LASTEXITCODE" }
& $ApkSigner verify --print-certs $Final
if ($LASTEXITCODE -ne 0) { throw "APK verification failed: $LASTEXITCODE" }
Write-Output "Built: $Final"
