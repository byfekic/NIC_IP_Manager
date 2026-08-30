"""Controlled privileged execution layer (specification sections 3, 29, 36).

Every configuration change runs through this module. The design constraints:

* The PowerShell script text is a **constant**. Operator input is never
  concatenated, interpolated or formatted into it, so shell/script injection
  is structurally impossible rather than merely filtered.
* Parameters travel as a JSON document on the child process's stdin and are
  parsed there with ``ConvertFrom-Json``, arriving as inert data.
* The script is written once to a private per-process temporary directory and
  run with ``-File``. It is deliberately *not* passed with ``-EncodedCommand``:
  base64 of UTF-16LE is about 2.7x the source size, which overruns the 32767
  character Windows command-line limit and fails with "The filename or
  extension is too long".
* ``shell=True`` is never used; arguments are always passed as a list.
* Results come back as a single JSON envelope, so nothing depends on parsing
  localized human-readable output.
* Every call has a timeout and a validated exit code.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any

from app.utils.errors import NetworkOperationError
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

CREATE_NO_WINDOW = 0x08000000

# ---------------------------------------------------------------------------
# The operation script. This text is fixed at build time and never modified.
# All values come from $params, which is parsed from stdin as JSON.
# ---------------------------------------------------------------------------
NET_OPS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
$WarningPreference     = 'SilentlyContinue'

$steps = New-Object System.Collections.ArrayList
function Step([string]$text) { [void]$steps.Add($text) }

function Emit($ok, $error, $code, $data) {
    $envelope = [ordered]@{
        ok    = [bool]$ok
        error = [string]$error
        code  = [string]$code
        steps = @($steps)
        data  = $data
    }
    Write-Output ($envelope | ConvertTo-Json -Depth 8 -Compress)
}

function Get-Ipv4State([int]$idx) {
    $addresses = @()
    Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        ForEach-Object {
            $addresses += [ordered]@{
                address      = [string]$_.IPAddress
                prefixLength = [int]$_.PrefixLength
                prefixOrigin = [string]$_.PrefixOrigin
                suffixOrigin = [string]$_.SuffixOrigin
                skipAsSource = [bool]$_.SkipAsSource
                store        = [string]$_.Store
            }
        }
    $routes = @()
    Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        ForEach-Object { $routes += [string]$_.NextHop }
    $dhcp = 'Unknown'
    $iface = Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($iface) { $dhcp = [string]$iface.Dhcp }
    return [ordered]@{
        ifIndex   = $idx
        dhcp      = $dhcp
        addresses = @($addresses)
        gateways  = @($routes)
    }
}

function Clear-Ipv4Addresses([int]$idx) {
    # Link-local APIPA addresses are managed by Windows itself and must not be
    # removed explicitly; Windows withdraws them when a real address appears.
    Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } |
        ForEach-Object {
            Remove-NetIPAddress -InputObject $_ -Confirm:$false -ErrorAction SilentlyContinue
        }
}

function Clear-DefaultRoutes([int]$idx) {
    Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-NetRoute -InputObject $_ -Confirm:$false -ErrorAction SilentlyContinue
        }
}

function Add-Ipv4Address($idx, $address, $prefix, $gateway, $skipAsSource) {
    $newArgs = @{
        InterfaceIndex = [int]$idx
        AddressFamily  = 'IPv4'
        IPAddress      = [string]$address
        PrefixLength   = [int]$prefix
        ErrorAction    = 'Stop'
    }
    if ($gateway) { $newArgs['DefaultGateway'] = [string]$gateway }
    if ($skipAsSource) { $newArgs['SkipAsSource'] = $true }
    [void](New-NetIPAddress @newArgs)
}

function Test-DhcpEnabled([int]$idx) {
    $iface = Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if (-not $iface) { return $false }
    return ([string]$iface.Dhcp -eq 'Enabled')
}

function Disable-Dhcp([int]$idx) {
    # Set-NetIPInterface can report success without taking effect (notably on
    # media-disconnected adapters), so the result is verified rather than
    # trusted. Returns $true only when DHCP is genuinely off afterwards.
    try {
        Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop
    } catch {
        # Already disabled, or the provider refused - the check below decides.
    }
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        if (-not (Test-DhcpEnabled $idx)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return (-not (Test-DhcpEnabled $idx))
}

function Get-WmiErrorText([int]$code) {
    switch ($code) {
        64 { 'Windows rejected the subnet mask.' }
        65 { 'Windows rejected the IP address.' }
        66 { 'Windows rejected the subnet mask.' }
        67 { 'Windows rejected the gateway address.' }
        70 { 'The IP address is already in use on this computer.' }
        84 { 'IP is not enabled on this adapter. Enable the adapter and try again.' }
        91 { 'Access denied. Administrator privileges are required.' }
        default { 'Windows configuration error ' + [string]$code + '.' }
    }
}

function Set-StaticViaWmi([int]$idx, $addresses, $masks, $gateway) {
    # EnableStatic assigns the addresses and clears DHCP in one atomic step.
    # It is the reliable route when Set-NetIPInterface will not switch DHCP
    # off, and it accepts every address at once so secondary addresses are
    # preserved rather than dropped.
    $cfg = Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration `
           -Filter ('InterfaceIndex=' + [string]$idx) -ErrorAction Stop
    if (-not $cfg) { throw 'Windows does not expose an IP configuration for this adapter.' }

    $result = Invoke-CimMethod -InputObject $cfg -MethodName EnableStatic -Arguments @{
        IPAddress  = [string[]]$addresses
        SubnetMask = [string[]]$masks
    } -ErrorAction Stop
    if ([int]$result.ReturnValue -ne 0) {
        throw (Get-WmiErrorText ([int]$result.ReturnValue))
    }

    if ($gateway) {
        $cfg = Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration `
               -Filter ('InterfaceIndex=' + [string]$idx) -ErrorAction Stop
        # GatewayCostMetric must be uint16; an int array is rejected outright.
        $gwResult = Invoke-CimMethod -InputObject $cfg -MethodName SetGateways -Arguments @{
            DefaultIPGateway  = [string[]]@([string]$gateway)
            GatewayCostMetric = [uint16[]]@(1)
        } -ErrorAction Stop
        if ([int]$gwResult.ReturnValue -ne 0) {
            throw (Get-WmiErrorText ([int]$gwResult.ReturnValue))
        }
    }
}

try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw) { Emit $false 'No parameters received.' 'no_params' $null; exit 0 }
    $p = $raw | ConvertFrom-Json
    $op = [string]$p.op

    switch ($op) {

        'ping' {
            Emit $true '' '' ([ordered]@{
                psVersion = $PSVersionTable.PSVersion.ToString()
                isAdmin   = ([Security.Principal.WindowsPrincipal] `
                             [Security.Principal.WindowsIdentity]::GetCurrent()
                            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
            })
        }

        'get_state' {
            Emit $true '' '' (Get-Ipv4State ([int]$p.ifIndex))
        }

        'set_static' {
            $idx = [int]$p.ifIndex

            Step 'Switching the interface off DHCP'
            $dhcpOff = Disable-Dhcp $idx

            # Every address this operation must end up with, primary first.
            $allAddresses = @([string]$p.ipAddress)
            $allMasks     = @([string]$p.subnetMask)
            if ($p.additional) {
                foreach ($extra in $p.additional) {
                    $allAddresses += [string]$extra.address
                    $allMasks     += [string]$extra.subnetMask
                }
            }

            if (-not $dhcpOff) {
                # New-NetIPAddress writes to the persistent store and refuses
                # with "Inconsistent parameters PolicyStore PersistentStore and
                # Dhcp Enabled" while DHCP is still on. Go straight to the WMI
                # method, which sets the addresses and clears DHCP atomically.
                Step 'DHCP could not be switched off directly; using the Windows configuration method'
                Clear-DefaultRoutes $idx
                Set-StaticViaWmi $idx $allAddresses $allMasks $p.gateway
                Step ('Assigned ' + [string]$p.ipAddress + '/' + [string]$p.subnetMask)
                Emit $true '' '' (Get-Ipv4State $idx)
                break
            }

            Step 'Removing existing default gateway routes'
            Clear-DefaultRoutes $idx

            Step 'Removing existing IPv4 addresses'
            Clear-Ipv4Addresses $idx
            Start-Sleep -Milliseconds 300

            Step ('Assigning ' + [string]$p.ipAddress + '/' + [string]$p.prefixLength)
            try {
                Add-Ipv4Address $idx $p.ipAddress $p.prefixLength $p.gateway $false
            } catch {
                # Last-resort fallback: DHCP looked off but Windows still
                # refused. Clear whatever was half-written and use WMI.
                Step ('Direct assignment failed (' + $_.Exception.Message + '); using the Windows configuration method')
                Clear-Ipv4Addresses $idx
                Clear-DefaultRoutes $idx
                Set-StaticViaWmi $idx $allAddresses $allMasks $p.gateway
                Emit $true '' '' (Get-Ipv4State $idx)
                break
            }

            if ($p.additional) {
                foreach ($extra in $p.additional) {
                    Step ('Restoring additional address ' + [string]$extra.address)
                    try {
                        Add-Ipv4Address $idx $extra.address $extra.prefixLength $null $true
                    } catch {
                        Step ('Could not restore ' + [string]$extra.address + ': ' + $_.Exception.Message)
                    }
                }
            }

            Emit $true '' '' (Get-Ipv4State $idx)
        }

        'set_dhcp' {
            $idx = [int]$p.ifIndex
            Step 'Removing static default gateway routes'
            Clear-DefaultRoutes $idx

            Step 'Enabling DHCP on the interface'
            Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Enabled -ErrorAction Stop

            Step 'Removing manually configured IPv4 addresses'
            Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.PrefixOrigin -eq 'Manual' -and $_.IPAddress -notlike '169.254.*' } |
                ForEach-Object {
                    Remove-NetIPAddress -InputObject $_ -Confirm:$false -ErrorAction SilentlyContinue
                }

            Step 'Requesting a DHCP lease'
            try {
                $cfg = Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration `
                       -Filter ('InterfaceIndex=' + [string]$idx) -ErrorAction Stop
                if ($cfg) {
                    [void](Invoke-CimMethod -InputObject $cfg -MethodName RenewDHCPLease -ErrorAction Stop)
                }
            } catch {
                # The adapter may be disconnected; Windows will lease when the
                # link returns. This is not an error for the operation itself.
                Step 'Lease request deferred (adapter not ready)'
            }

            Emit $true '' '' (Get-Ipv4State $idx)
        }

        'restore' {
            $idx = [int]$p.ifIndex
            if ([bool]$p.dhcpEnabled) {
                Step 'Restoring DHCP'
                Clear-DefaultRoutes $idx
                Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Enabled -ErrorAction Stop
                Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                    Where-Object { $_.PrefixOrigin -eq 'Manual' -and $_.IPAddress -notlike '169.254.*' } |
                    ForEach-Object {
                        Remove-NetIPAddress -InputObject $_ -Confirm:$false -ErrorAction SilentlyContinue
                    }
                try {
                    $cfg = Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration `
                           -Filter ('InterfaceIndex=' + [string]$idx) -ErrorAction Stop
                    if ($cfg) {
                        [void](Invoke-CimMethod -InputObject $cfg -MethodName RenewDHCPLease -ErrorAction Stop)
                    }
                } catch { Step 'Lease request deferred' }
            } else {
                Step 'Restoring the previous static configuration'
                $dhcpOff = Disable-Dhcp $idx

                $gateway = $null
                if ($p.gateways -and @($p.gateways).Count -gt 0) { $gateway = [string]@($p.gateways)[0] }

                $restoreAddresses = @()
                $restoreMasks     = @()
                foreach ($addr in @($p.addresses)) {
                    $restoreAddresses += [string]$addr.address
                    $restoreMasks     += [string]$addr.subnetMask
                }

                if (-not $dhcpOff) {
                    # Same constraint as set_static: New-NetIPAddress will not
                    # write a persistent address while DHCP is still enabled.
                    Step 'DHCP could not be switched off directly; using the Windows configuration method'
                    Clear-DefaultRoutes $idx
                    if ($restoreAddresses.Count -gt 0) {
                        Set-StaticViaWmi $idx $restoreAddresses $restoreMasks $gateway
                    }
                } else {
                    Clear-DefaultRoutes $idx
                    Clear-Ipv4Addresses $idx
                    Start-Sleep -Milliseconds 300

                    $first = $true
                    foreach ($addr in @($p.addresses)) {
                        Step ('Restoring ' + [string]$addr.address + '/' + [string]$addr.prefixLength)
                        if ($first) {
                            Add-Ipv4Address $idx $addr.address $addr.prefixLength $gateway $false
                            $first = $false
                        } else {
                            Add-Ipv4Address $idx $addr.address $addr.prefixLength $null $true
                        }
                    }
                }
            }
            Emit $true '' '' (Get-Ipv4State $idx)
        }

        default {
            Emit $false ('Unknown operation: ' + $op) 'unknown_op' $null
        }
    }
}
catch {
    $code = ''
    try { if ($_.FullyQualifiedErrorId) { $code = [string]$_.FullyQualifiedErrorId } } catch {}
    Emit $false ([string]$_.Exception.Message) $code $null
}
"""


@dataclass
class PSResult:
    """Structured outcome of one privileged operation."""

    ok: bool
    error: str = ""
    code: str = ""
    data: Any = None
    steps: list[str] = field(default_factory=list)
    exit_code: int = 0
    raw_stdout: str = ""
    raw_stderr: str = ""


def _powershell_executable() -> str:
    """Resolve powershell.exe from SystemRoot rather than trusting PATH."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    return candidate if os.path.isfile(candidate) else "powershell.exe"


class PowerShellRunner:
    """Executes the fixed network-operations script with JSON parameters."""

    def __init__(self, timeout: float = 90.0) -> None:
        self.timeout = timeout
        self._script_dir: str | None = None
        self._script_path: str | None = None
        self._script_lock = threading.Lock()

    def _ensure_script(self) -> str:
        """Materialise the constant script in a private temporary directory.

        The directory is created by ``mkdtemp``, so on Windows it inherits an
        ACL granting access to this user only. The content is a fixed constant
        - operator input never reaches it.
        """
        with self._script_lock:
            if self._script_path and os.path.isfile(self._script_path):
                return self._script_path
            self._script_dir = tempfile.mkdtemp(prefix="ip_changer_")
            path = os.path.join(self._script_dir, "netops.ps1")
            # utf-8-sig: the BOM makes Windows PowerShell 5.1 read the file as
            # UTF-8 rather than the legacy ANSI code page.
            with open(path, "w", encoding="utf-8-sig") as handle:
                handle.write(NET_OPS_SCRIPT)
            self._script_path = path
            atexit.register(self._cleanup)
            log.debug("Network operations script written to %s", path)
            return path

    def _cleanup(self) -> None:
        if self._script_dir:
            shutil.rmtree(self._script_dir, ignore_errors=True)
            self._script_dir = None
            self._script_path = None

    def run(self, op: str, timeout: float | None = None, **params: Any) -> PSResult:
        """Invoke one operation. Never raises for operational failures."""
        payload = dict(params)
        payload["op"] = op
        try:
            script_path = self._ensure_script()
        except OSError as exc:
            log.error("Could not write the network operations script: %s", exc)
            return PSResult(
                ok=False,
                error="The network operations script could not be prepared.",
                code="no_script",
                raw_stderr=str(exc),
            )

        command = [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
        ]

        log.debug("PowerShell op=%s params=%s", op, {k: v for k, v in params.items()})
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                creationflags=CREATE_NO_WINDOW,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.error("PowerShell op=%s timed out", op)
            return PSResult(
                ok=False,
                error="Windows did not respond within the allowed time.",
                code="timeout",
                raw_stderr=str(exc),
            )
        except OSError as exc:
            log.error("PowerShell could not be started: %s", exc)
            return PSResult(
                ok=False,
                error="Windows PowerShell could not be started.",
                code="no_powershell",
                raw_stderr=str(exc),
            )

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        envelope = self._extract_envelope(stdout)
        if envelope is None:
            log.error(
                "PowerShell op=%s returned no JSON envelope (exit=%s) stdout=%r stderr=%r",
                op,
                completed.returncode,
                stdout[:400],
                stderr[:400],
            )
            return PSResult(
                ok=False,
                error="Windows returned an unreadable response.",
                code="bad_response",
                exit_code=completed.returncode,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )

        result = PSResult(
            ok=bool(envelope.get("ok")),
            error=str(envelope.get("error") or ""),
            code=str(envelope.get("code") or ""),
            data=envelope.get("data"),
            steps=[str(s) for s in (envelope.get("steps") or [])],
            exit_code=completed.returncode,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )
        if not result.ok:
            log.warning("PowerShell op=%s failed: %s (%s)", op, result.error, result.code)
        else:
            log.info("PowerShell op=%s succeeded; steps=%s", op, result.steps)
        return result

    @staticmethod
    def _extract_envelope(stdout: str) -> dict | None:
        """Pull the JSON envelope out of stdout, tolerating stray output."""
        if not stdout:
            return None
        try:
            parsed = json.loads(stdout)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        # Fall back to the last JSON object in the stream.
        start = stdout.find("{")
        while start != -1:
            try:
                parsed = json.loads(stdout[start:])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                start = stdout.find("{", start + 1)
        return None

    def require(self, op: str, **params: Any) -> PSResult:
        """Run an operation and raise a user-presentable error on failure."""
        result = self.run(op, **params)
        if not result.ok:
            raise NetworkOperationError(
                "Unable to change the network configuration.",
                translate_error(result.error, result.code),
                f"op={op} code={result.code} error={result.error}",
            )
        return result


# --------------------------------------------------------------------------
# Error translation (specification section 22): Windows/PowerShell messages are
# turned into something an operator can act on.
# --------------------------------------------------------------------------
_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "access is denied",
        "Windows denied the change. Administrator privileges are required.",
    ),
    (
        "requested operation requires elevation",
        "Windows denied the change. Administrator privileges are required.",
    ),
    (
        "already exists",
        "That IP address is already configured on this computer. "
        "Choose a different address, or remove the existing one first.",
    ),
    (
        "no matching",
        "Windows could not find the network interface. "
        "It may have been disabled or removed.",
    ),
    (
        "not found",
        "Windows could not find the network interface. "
        "It may have been disabled or removed.",
    ),
    (
        "invalid parameter",
        "Windows rejected the requested configuration as invalid. "
        "Check the IP address, subnet mask and gateway.",
    ),
    (
        "the object already exists",
        "That IP address is already configured on this computer.",
    ),
    (
        "inactive",
        "The interface is not active. Connect the cable or enable the adapter and try again.",
    ),
    (
        "dhcp",
        "Windows could not complete the DHCP operation on this adapter.",
    ),
)


def translate_error(message: str, code: str = "") -> str:
    """Map a raw Windows/PowerShell message to an operator-facing explanation."""
    text = (message or "").lower()
    if code == "timeout":
        return (
            "Windows did not respond in time. The network stack may be busy. "
            "Check the current configuration before retrying."
        )
    if code == "no_script":
        return (
            "The helper script could not be written to the temporary folder. "
            "Check that your user profile and TEMP folder are accessible."
        )
    if code == "no_powershell":
        return (
            "Windows PowerShell could not be started, so the configuration "
            "cannot be changed on this system."
        )
    for needle, explanation in _ERROR_PATTERNS:
        if needle in text:
            return explanation
    if message:
        return f"Windows reported: {message}"
    return "Windows rejected the requested configuration."
