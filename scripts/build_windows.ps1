$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
$version = if ($env:DIGIFLY_RELEASE_VERSION) { $env:DIGIFLY_RELEASE_VERSION } else { "0.1.0-alpha.20261015.1" }
if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z.-]+$') { throw "Unsafe release version" }
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("digifly-windows-" + [guid]::NewGuid().ToString("N"))
$dist = Join-Path $project "dist"
if (-not (Test-Path $python)) { throw "Run scripts/setup_dev.ps1 before building." }
New-Item -ItemType Directory -Path $dist -Force | Out-Null
# Fresh allowlisted inputs, never a recursive copy of the user's checkout.
& $python "$PSScriptRoot\stage_windows_source.py" --source $project --destination $stage
if ($LASTEXITCODE -ne 0) { throw "Source staging failed." }
@{ version = $version; build = $env:DIGIFLY_BUILD_NUMBER; source_commit = $env:GITHUB_SHA; platform = "Windows-x86_64"; arbor = "deferred"; native_simulators = "not_qualified" } | ConvertTo-Json | Set-Content (Join-Path $stage "digifly_build.json") -Encoding ascii
$iconSource = Join-Path $stage "src\digifly_app\assets\digifly_icon.png"
$iconTarget = Join-Path $stage "src\digifly_app\assets\digifly_icon.ico"
& $python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2], sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])" $iconSource $iconTarget
if ($LASTEXITCODE -ne 0) { throw "Could not create Windows icon." }
$env:PYTHONPATH = Join-Path $stage "src"
Push-Location $stage
try {
    # Reject frozen-asset inventory/path drift before the expensive compiler.
    & $python -c "from pathlib import Path; from digifly_app.workers.pdcm_bionet_app_worker import validate_sources; from digifly_app.workers.pdcm_bionet_scalable_app_worker import validate_sources as v4; validate_sources(Path('src/digifly_app/pdcm_bionet/primary_v3')); v4(Path('src/digifly_app/pdcm_bionet/primary_v4'))"
    if ($LASTEXITCODE -ne 0) { throw "Frozen assets failed precompile validation." }
    $nuitkaArgs = @(
        "-m", "nuitka", "main.py", "--standalone", "--enable-plugin=pyside6", "--msvc=latest",
        "--output-dir=deployment", "--assume-yes-for-downloads", "--lto=no", "--jobs=4",
        "--windows-console-mode=disable", "--windows-icon-from-ico=$iconTarget",
        "--noinclude-qt-translations", "--nofollow-import-to=neuron,arbor,bmtk,numpy,h5py,netpyne,duckdb,pytest",
        "--include-data-file=src/digifly_app/assets/digifly_icon.png=digifly_app/assets/digifly_icon.png",
        "--include-data-dir=mechanisms=mechanisms", "--include-data-dir=presets=presets",
        "--include-data-dir=schemas=schemas", "--include-data-dir=docs=docs",
        "--include-data-file=digifly_build.json=digifly_build.json",
        "--include-data-file=README.md=README.md", "--include-data-file=LICENSE=LICENSE",
        "--include-data-file=THIRD_PARTY_NOTICES.md=THIRD_PARTY_NOTICES.md",
        "--include-data-dir=src/digifly_app/pdcm_bionet=digifly_app/pdcm_bionet"
    )
    Get-ChildItem "src\digifly_app\workers\*.py" | ForEach-Object {
        $nuitkaArgs += "--include-data-file=$($_.FullName)=digifly_app/workers/$($_.Name)"
    }
    & $python @nuitkaArgs
    if ($LASTEXITCODE -ne 0) { throw "Windows compilation failed. Staging retained: $stage" }
    $bundle = Join-Path $stage "deployment\main.dist"
    $exe = Join-Path $bundle "main.exe"
    if (-not (Test-Path $exe)) { throw "Windows deployment did not create main.exe." }
    & $python "scripts\stage_pdcm_bionet_assets.py" --source "src\digifly_app\pdcm_bionet\primary_v3" --package (Join-Path $bundle "digifly_app")
    if ($LASTEXITCODE -ne 0) { throw "Frozen v3 assets failed validation." }
    & $python "scripts\stage_pdcm_bionet_scalable_assets.py" --source "src\digifly_app\pdcm_bionet\primary_v4" --package (Join-Path $bundle "digifly_app")
    if ($LASTEXITCODE -ne 0) { throw "Frozen v4 assets failed validation." }
    Rename-Item $exe "Digifly Workstation.exe"
    New-Item -ItemType Directory -Path (Join-Path $bundle "digifly_app\workers") -Force | Out-Null
    Get-ChildItem "src\digifly_app\workers\*.py" | Copy-Item -Destination (Join-Path $bundle "digifly_app\workers")
    New-Item -ItemType Directory -Path (Join-Path $bundle "digifly_app\core"), (Join-Path $bundle "scripts") -Force | Out-Null
    Copy-Item "src\digifly_app\core\pdcm_bionet_5pct_app.py" (Join-Path $bundle "digifly_app\core")
    Copy-Item "src\digifly_app\core\pdcm_campaign_results.py" (Join-Path $bundle "digifly_app\workers\pdcm_campaign_results.py")
    Get-ChildItem "scripts\*.py", "scripts\*.sh" | Copy-Item -Destination (Join-Path $bundle "scripts")
    Copy-Item "docs\WINDOWS_TESTER.md" (Join-Path $bundle "START HERE.md")
    $requiredWorkers = @("generic_experiment_worker.py", "arbor_escape_siz_worker.py", "bmtk_bionet_worker.py", "escape_siz_worker.py", "pdcm_netpyne_worker.py", "pdcm_netpyne_mpi_worker.py", "pair_graph_import_worker.py")
    foreach ($worker in $requiredWorkers) {
        if (-not (Test-Path (Join-Path $bundle "digifly_app\workers\$worker"))) { throw "Packaged simulator worker is missing: $worker" }
    }
    & $python "scripts\collect_windows_notices.py" --bundle $bundle
    if ($LASTEXITCODE -ne 0) { throw "Dependency license/notice inventory failed." }
    if ($env:DIGIFLY_NEURON_PAYLOAD) {
        $offline = Join-Path $bundle "installers\neuron"
        New-Item -ItemType Directory -Path $offline | Out-Null
        Copy-Item (Join-Path $env:DIGIFLY_NEURON_PAYLOAD "*") $offline
        & $python -c "from pathlib import Path; import sys; from digifly_app.core.windows_neuron_bundle import validate_manifest; validate_manifest(Path(sys.argv[1]))" $offline
        if ($LASTEXITCODE -ne 0) { throw "Included NEURON payload failed source/hash validation." }
    }
    & $python "scripts\windows_package.py" seal --bundle $bundle --source $stage --version $version
    if ($LASTEXITCODE -ne 0) { throw "Windows package audit failed." }
    $archive = Join-Path $dist "Digifly-Workstation-$version-Windows-x86_64.zip"
    if (Test-Path $archive) { throw "Refusing to overwrite an existing Windows archive." }
    & $python "scripts\windows_package.py" archive --bundle $bundle --archive $archive
    if ($LASTEXITCODE -ne 0) { throw "Windows archive failed validation." }
    Write-Output "Validated Windows ZIP: $archive"
}
finally { Pop-Location }
# Unique staging is retained for diagnostics; previous builds/data are untouched.
