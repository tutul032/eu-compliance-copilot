# Builds the eucc wheel and uploads it to the artifacts volume, where the Databricks App installs
# it from (app/uv-requirements.txt). Run from anywhere, before `databricks bundle deploy -t dev`
# when the app needs new eucc code:
#
#     ./scripts/publish_app_wheel.ps1
#
# The volume path is fixed (uv-requirements.txt cannot use variables): if the version in
# pyproject.toml changes, the wheel name changes and this script stops; update the name here and
# in app/uv-requirements.txt.
$ErrorActionPreference = "Stop"

$Wheel = "eucc-0.0.1-py3-none-any.whl"
$Target = "dbfs:/Volumes/workspace/compliance_dev/artifacts/$Wheel"
$DatabricksProfile = "free"

Push-Location (Join-Path $PSScriptRoot "..")
try {
    uv build --wheel
    if ($LASTEXITCODE -ne 0) { throw "uv build failed (exit code $LASTEXITCODE)." }

    $Path = Join-Path "dist" $Wheel
    if (-not (Test-Path $Path)) {
        throw "$Path not found: has the version in pyproject.toml changed? Update `$Wheel and app/uv-requirements.txt."
    }

    databricks fs cp --overwrite $Path $Target -p $DatabricksProfile
    if ($LASTEXITCODE -ne 0) { throw "Upload failed (exit code $LASTEXITCODE)." }
    Write-Host "Uploaded $Path to $Target"
}
finally {
    Pop-Location
}
