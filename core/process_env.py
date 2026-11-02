"""Encode a child-process environment without shell-interpolating paths or values."""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path


def powershell_arguments(file: str, arguments: str, cwd: Path, environment: dict) -> str:
    payload = json.dumps({"file": file, "arguments": arguments, "cwd": str(cwd),
                          "environment": environment}, ensure_ascii=False)
    data = base64.b64encode(gzip.compress(payload.encode("utf-8"))).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $memory = New-Object IO.MemoryStream(,[Convert]::FromBase64String('{data}'))
    $gzip = New-Object IO.Compression.GZipStream($memory, [IO.Compression.CompressionMode]::Decompress)
    $reader = New-Object IO.StreamReader($gzip, [Text.Encoding]::UTF8)
    $data = $reader.ReadToEnd() | ConvertFrom-Json
    $reader.Dispose()
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $data.file
    if (-not [IO.Path]::IsPathRooted($data.file)) {{
        $resolved = $false
        foreach ($folder in ($data.environment.PATH -split [IO.Path]::PathSeparator)) {{
            if ([string]::IsNullOrWhiteSpace($folder)) {{ continue }}
            $candidate = [IO.Path]::Combine($folder.Trim('"'), $data.file)
            if ([IO.File]::Exists($candidate)) {{
                $info.FileName = $candidate
                $resolved = $true
                break
            }}
        }}
        if (-not $resolved) {{ throw ('环境中找不到程序: ' + $data.file) }}
    }}
    $info.Arguments = $data.arguments
    $info.WorkingDirectory = $data.cwd
    $info.UseShellExecute = $false
    $info.EnvironmentVariables.Clear()
    foreach ($property in $data.environment.PSObject.Properties) {{
        $info.EnvironmentVariables[$property.Name] = [string]$property.Value
    }}
    $child = [Diagnostics.Process]::Start($info)
    $child.WaitForExit()
    exit $child.ExitCode
}} catch {{
    Write-Host ('启动失败: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # Windows' native command line limit also applies to ShellExecute.
    result = "-NoLogo -NoProfile -EncodedCommand " + encoded
    if len(result) > 30000:
        raise ValueError("运行环境或命令过长，请缩短命令或清理 PATH 后重试。")
    return result
