$ErrorActionPreference = "Stop"

# Run from repo root:
#   powershell -ExecutionPolicy Bypass -File .\\tk_gui\\build_onefile.ps1

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repo

$venvPy = Join-Path $repo ".venv\\Scripts\\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }

if (Get-Process -Name "oai-team-gui" -ErrorAction SilentlyContinue) {
  throw "Detected running oai-team-gui.exe. Please close it before packaging (dist/oai-team-gui.exe is locked)."
}

& $py -m pip --version
if ($LASTEXITCODE -ne 0) {
  & $py -m ensurepip --upgrade
  if ($LASTEXITCODE -ne 0) { throw "ensurepip failed. Please check your Python environment." }
}

& $py -m pip install -U pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller" }

& $py -m PyInstaller --noconfirm --clean --onefile --noconsole --name oai-team-gui `
  --specpath "build" `
  --add-data "..\\config.toml.example;." `
  --add-data "..\\team.json.example;." `
  "gui_main.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "Done: dist/oai-team-gui.exe"
