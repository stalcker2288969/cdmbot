$ErrorActionPreference = 'Stop'

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE."
    }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $uvBin = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else {
        Join-Path $env:USERPROFILE '.local\bin'
    }
    $uvCommand = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue
    $uv = if ($uvCommand) { $uvCommand.Source } else { Join-Path $uvBin 'uv.exe' }
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        Write-Host 'Installing uv for the current user...'
        $bootstrap = Join-Path ([IO.Path]::GetTempPath()) ('cdmbot-uv-' + [guid]::NewGuid() + '.ps1')
        $previousInstallDir = $env:UV_INSTALL_DIR
        $previousModifyPath = $env:UV_NO_MODIFY_PATH
        try {
            # Windows PowerShell can otherwise negotiate an obsolete TLS version.
            $previousTls = [Net.ServicePointManager]::SecurityProtocol
            try {
                [Net.ServicePointManager]::SecurityProtocol = $previousTls -bor [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $bootstrap
            } finally {
                [Net.ServicePointManager]::SecurityProtocol = $previousTls
            }
            $env:UV_INSTALL_DIR = $uvBin
            $env:UV_NO_MODIFY_PATH = '1'
            Invoke-Checked 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $bootstrap)
        } finally {
            $env:UV_INSTALL_DIR = $previousInstallDir
            $env:UV_NO_MODIFY_PATH = $previousModifyPath
            if (Test-Path -LiteralPath $bootstrap) {
                Remove-Item -LiteralPath $bootstrap -Force
            }
        }
    }

    Invoke-Checked $uv @('tool', 'install', '--python', '3.12', '--reinstall-package', 'cdmbot', '.[managed-java]')
    $toolBin = (& $uv tool dir --bin)
    if ($LASTEXITCODE -ne 0 -or -not $toolBin) {
        throw 'Cannot determine the CDMBot command directory.'
    }
    $command = Join-Path $toolBin 'cdmbot.exe'
    Invoke-Checked $command @('examples/cdm8/inputs.asm', 'examples/cdm8/inputs.yaml')
    Invoke-Checked $command @('examples/cdm16/add.asm', 'examples/cdm16/add.yaml', '--case', 'simple')

    if ($env:CDMBOT_NO_PATH_UPDATE -ne '1') {
        & $uv tool update-shell
        if ($LASTEXITCODE -eq 0) {
            Write-Host 'Open a new terminal and run cdmbot --help.'
        } else {
            Write-Host "To enable the cdmbot command, add $toolBin to your PATH."
        }
    }
    Write-Host "CDMBot is ready: $command"
} catch {
    [Console]::Error.WriteLine("CDMBot installation failed: $($_.Exception.Message)")
    exit 1
} finally {
    Pop-Location
}
