$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
$spec = Join-Path $project "pysidedeploy.windows.spec"
$version = if ($env:DIGIFLY_RELEASE_VERSION) { $env:DIGIFLY_RELEASE_VERSION } else { "0.1.0" }
$stage = Join-Path $env:RUNNER_TEMP "digifly-windows-build"
$dist = Join-Path $project "dist"

if (-not (Test-Path $python)) { throw "Run scripts/setup_dev.ps1 before building." }
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage, $dist -Force | Out-Null
robocopy $project $stage /E /XD .git .venv deployment dist __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not stage the Windows build." }
$buildNumber = if ($env:DIGIFLY_BUILD_NUMBER) { $env:DIGIFLY_BUILD_NUMBER } else { "1" }
$buildInfo = @{ version = $version; build = $buildNumber; built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
$buildInfo | ConvertTo-Json -Compress | Set-Content (Join-Path $stage "digifly_build.json") -Encoding ascii
Copy-Item (Join-Path $project ".venv") (Join-Path $stage ".venv") -Recurse
$iconSource = Join-Path $stage "src\digifly_app\assets\digifly_icon.png"
$iconTarget = Join-Path $stage "src\digifly_app\assets\digifly_icon.ico"
& (Join-Path $stage ".venv\Scripts\python.exe") -c "from PIL import Image; Image.open(r'$iconSource').save(r'$iconTarget', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) { throw "Could not create the Windows application icon." }

Push-Location $stage
try {
    $nuitkaArgs = @(
        "-m", "nuitka", "main.py", "--standalone", "--enable-plugin=pyside6",
        "--output-dir=deployment", "--assume-yes-for-downloads", "--lto=no", "--jobs=4",
        "--windows-console-mode=disable", "--windows-icon-from-ico=$iconTarget",
        "--include-data-file=src/digifly_app/assets/digifly_icon.png=digifly_app/assets/digifly_icon.png",
        "--include-data-dir=src/digifly_app/workers=digifly_app/workers",
        "--include-data-dir=mechanisms=mechanisms", "--include-data-dir=presets=presets",
        "--include-data-dir=schemas=schemas", "--include-data-dir=docs=docs",
        "--include-data-file=digifly_build.json=digifly_build.json",
        "--include-data-file=README.md=README.md", "--include-data-file=LICENSE=LICENSE"
    )
    & ".venv\Scripts\python.exe" @nuitkaArgs
    if ($LASTEXITCODE -ne 0) { throw "Windows application compilation failed." }
    $bundle = Join-Path $stage "deployment\main.dist"
    $exe = Join-Path $bundle "main.exe"
    if (-not (Test-Path $exe)) { throw "Windows deployment did not create main.exe." }
    Rename-Item $exe "Digifly Workstation.exe"
    $launcherDir = Join-Path $bundle "runtimes\docker"
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
    & ".venv\Scripts\python.exe" -m nuitka --onefile --output-dir=$launcherDir --output-filename="digifly-python.exe" "scripts\docker_python_launcher.py"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $launcherDir "digifly-python.exe"))) {
        throw "Could not build the Docker simulator launcher."
    }
    $archive = Join-Path $dist "Digifly-Workstation-$version-Windows-x86_64.zip"
    if (Test-Path $archive) { Remove-Item -Force $archive }
    Compress-Archive -Path (Join-Path $bundle "*") -DestinationPath $archive -CompressionLevel Optimal
    (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower() + "  " + (Split-Path $archive -Leaf) | Set-Content "$archive.sha256" -Encoding ascii
}
finally { Pop-Location }
