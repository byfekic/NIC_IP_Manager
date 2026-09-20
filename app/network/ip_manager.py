"""The network configuration engine (specification sections 28, 55).

Every change is treated as a transaction:

    READ -> SNAPSHOT -> VALIDATE -> APPLY -> WAIT -> VERIFY -> SUCCESS
                                       |
                                     FAIL -> ROLLBACK -> VERIFY ROLLBACK

The GUI never executes PowerShell or netsh itself; it only calls this class.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from app.models.adapter import Adapter
from app.models.configuration import (
    ConfigMode,
    ConfigurationSnapshot,
    IPConfiguration,
    OperationResult,
    ResultStatus,
    prefix_to_mask,
)
from app.network.adapter_manager import AdapterManager
from app.network.powershell import PowerShellRunner, translate_error
from app.network.validator import validate
from app.network.verifier import (
    ConflictResult,
    ConflictStatus,
    check_ip_conflict,
    verify_configuration,
)
from app.storage.journal import OperationJournal
from app.utils.errors import IPChangerError, humanize
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

ProgressCallback = Optional[Callable[[str], None]]


class _ConflictProbe:
    """Runs the ARP probe alongside the change instead of after it.

    SendARP takes about three seconds to conclude that nobody answered, and
    nobody answering is the ordinary, good case - so running it in sequence
    cost more than the change itself. Started before the address is assigned
    it also answers the more meaningful question: a reply then is certainly
    another device rather than this machine.
    """

    def __init__(self, ip_address: str, own_macs: set[str]) -> None:
        self._result: Optional[ConflictResult] = None
        self._thread = threading.Thread(
            target=self._run, args=(ip_address, own_macs), daemon=True
        )

    def start(self) -> "_ConflictProbe":
        self._thread.start()
        return self

    def _run(self, ip_address: str, own_macs: set[str]) -> None:
        try:
            self._result = check_ip_conflict(ip_address, own_macs=own_macs)
        except Exception:  # informational only; never fail the operation
            log.exception("Address conflict probe failed")

    def result(self, timeout: float) -> ConflictResult:
        self._thread.join(timeout)
        if self._result is None:
            return ConflictResult(
                ConflictStatus.UNKNOWN,
                detail="The conflict check did not finish in time.",
            )
        return self._result


class NetworkManager:
    """Applies, verifies and rolls back IPv4 configurations."""

    # Caps, not costs. Windows applies a change asynchronously, so the adapter
    # is re-read until it shows what was asked for instead of sleeping for a
    # fixed period: a static address is normally visible within a few tens of
    # milliseconds, and always waiting 1.5s for it made every change feel slow.
    STATIC_SETTLE_TIMEOUT = 2.0
    DHCP_SETTLE_TIMEOUT = 15.0
    SETTLE_POLL_INTERVAL = 0.05

    # Backstop for the conflict probe, which has normally finished by the time
    # the change has been applied and verified.
    CONFLICT_PROBE_TIMEOUT = 6.0

    def __init__(
        self,
        adapter_manager: Optional[AdapterManager] = None,
        runner: Optional[PowerShellRunner] = None,
        journal: Optional[OperationJournal] = None,
    ) -> None:
        self.adapters = adapter_manager or AdapterManager()
        self.runner = runner or PowerShellRunner()
        self.journal = journal or OperationJournal()

    # ------------------------------------------------------------- reading
    def list_adapters(self, include_loopback: bool = False) -> list[Adapter]:
        return self.adapters.list_adapters(include_loopback=include_loopback)

    def get_configuration(self, adapter: Adapter) -> Optional[Adapter]:
        """Re-read one adapter's live configuration."""
        return self.adapters.refresh(adapter)

    def snapshot(self, adapter: Adapter) -> ConfigurationSnapshot:
        return ConfigurationSnapshot.from_adapter(adapter)

    # ------------------------------------------------------------- dry run
    def dry_run(self, adapter: Adapter, config: IPConfiguration) -> OperationResult:
        """Report what would change, without touching anything (section 38)."""
        lines = [f"Adapter:  {adapter.friendly_name}  ({adapter.kind.value})"]
        current = adapter.primary_ipv4

        if config.is_dhcp:
            lines.append("")
            lines.append(f"Mode:     {adapter.mode_label}  ->  DHCP")
            if not adapter.effective_dhcp and current is not None:
                lines.append(f"IP:       {current.address}  ->  (assigned by DHCP)")
                lines.append(f"Subnet:   {current.subnet_mask}  ->  (assigned by DHCP)")
                gateway = adapter.primary_gateway or "none"
                lines.append(f"Gateway:  {gateway}  ->  (assigned by DHCP)")
        else:
            lines.append("")
            lines.append(f"Mode:     {adapter.mode_label}  ->  Static")
            old_ip = current.address if current else "none"
            old_mask = current.subnet_mask if current else "none"
            old_gw = adapter.primary_gateway or "none"
            lines.append(f"IP:       {old_ip}  ->  {config.ip_address}")
            lines.append(f"Subnet:   {old_mask}  ->  {config.subnet_mask}")
            lines.append(f"Gateway:  {old_gw}  ->  {config.gateway or 'none'}")

        extra = [a.address for a in adapter.routable_ipv4 if not current or a.address != current.address]
        if extra:
            lines.append("")
            lines.append(f"Preserved additional addresses: {', '.join(extra)}")

        result = validate(config, adapter)
        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  - {w.message}" for w in result.warnings)
        if result.errors:
            lines.append("")
            lines.append("Errors (the configuration would be rejected):")
            lines.extend(f"  - {e.message}" for e in result.errors)

        log.info("Dry run for %s: %s", adapter.friendly_name, config.describe())
        return OperationResult(
            status=ResultStatus.DRY_RUN,
            message="Dry run - nothing was changed.",
            detail="\n".join(lines),
            snapshot=self.snapshot(adapter),
        )

    # --------------------------------------------------------------- apply
    def apply_configuration(
        self,
        adapter: Adapter,
        config: IPConfiguration,
        progress: ProgressCallback = None,
        preserve_additional: bool = True,
        check_conflict: bool = True,
        auto_rollback: bool = False,
    ) -> OperationResult:
        """Run the full transaction for one adapter.

        The caller is responsible for having obtained the operator's
        confirmation; this method performs no dialogs.
        """
        def step(text: str) -> None:
            log.info("[%s] %s", adapter.friendly_name, text)
            if progress:
                progress(text)

        steps: list[str] = []

        # -- READ: make sure the adapter is still there and still what we think
        step("Re-reading the network adapter")
        live = self.adapters.refresh(adapter)
        if live is None:
            log.error("Adapter %s disappeared before the change", adapter.friendly_name)
            return OperationResult(
                status=ResultStatus.FAILED,
                message="The network adapter is no longer available.",
                detail=(
                    f"'{adapter.friendly_name}' could not be found. It may have been "
                    "disabled, unplugged or removed. No changes were made."
                ),
            )
        adapter = live
        steps.append("Adapter confirmed")

        # -- VALIDATE (again, immediately before touching anything)
        result = validate(config, adapter)
        if not result.is_valid:
            log.warning("Refusing to apply an invalid configuration: %s", result.error_text())
            return OperationResult(
                status=ResultStatus.FAILED,
                message="The configuration is not valid.",
                detail=result.error_text(),
            )

        # -- SNAPSHOT
        step("Saving the current configuration")
        snapshot = self.snapshot(adapter)
        steps.append(f"Snapshot: {snapshot.describe()}")
        self.journal.begin(adapter.friendly_name, adapter.guid, config, snapshot)

        # -- CONFLICT PROBE: started here so its three seconds overlap the
        # change rather than being added to it.
        probe: Optional[_ConflictProbe] = None
        if check_conflict and not config.is_dhcp:
            own = {a.mac for a in self.adapters.list_adapters(include_loopback=True) if a.mac}
            probe = _ConflictProbe(config.ip_address, own).start()

        try:
            # -- APPLY
            if config.is_dhcp:
                step("Switching the adapter to DHCP")
                ps_result = self.runner.run("set_dhcp", ifIndex=adapter.if_index)
            else:
                additional = []
                if preserve_additional:
                    # Section 32 protects *secondary* addresses. The primary is
                    # the one being changed, so it must not be carried over -
                    # otherwise changing an address would only ever add one and
                    # the old address would linger.
                    current_primary = adapter.primary_ipv4
                    replaced = {config.ip_address}
                    if current_primary is not None:
                        replaced.add(current_primary.address)
                    additional = [
                        {
                            "address": a.address,
                            "prefixLength": a.prefix_length,
                            # The WMI path assigns addresses by dotted mask,
                            # so send both forms.
                            "subnetMask": a.subnet_mask,
                        }
                        for a in adapter.routable_ipv4
                        if a.address not in replaced
                    ]
                step(f"Assigning {config.ip_address} / {config.subnet_mask}")
                ps_result = self.runner.run(
                    "set_static",
                    ifIndex=adapter.if_index,
                    ipAddress=config.ip_address,
                    prefixLength=int(config.prefix_length),
                    subnetMask=config.subnet_mask,
                    gateway=config.gateway or None,
                    additional=additional,
                )

            steps.extend(ps_result.steps)

            if not ps_result.ok:
                log.error(
                    "Apply failed on %s: %s (%s)",
                    adapter.friendly_name,
                    ps_result.error,
                    ps_result.code,
                )
                failure = OperationResult(
                    status=ResultStatus.FAILED,
                    message="Unable to change the network configuration.",
                    detail=translate_error(ps_result.error, ps_result.code),
                    technical=f"code={ps_result.code} error={ps_result.error}",
                    snapshot=snapshot,
                    steps=steps,
                )
                if auto_rollback:
                    return self._rollback_after_failure(failure, snapshot, step)
                return failure

            # -- WAIT
            step("Waiting for Windows to update the network stack")
            resulting = self._settle(adapter, config, step)

            # -- VERIFY
            step("Verifying the configuration")
            if resulting is None:
                outcome_result = OperationResult(
                    status=ResultStatus.VERIFY_FAILED,
                    message="Configuration could not be verified.",
                    detail=(
                        "The adapter could not be read back after the change. "
                        "It may have been disconnected."
                    ),
                    snapshot=snapshot,
                    steps=steps,
                )
                if auto_rollback:
                    return self._rollback_after_failure(outcome_result, snapshot, step)
                return outcome_result

            outcome = verify_configuration(resulting, config)
            steps.append(f"Verification: {outcome.describe()}")

            if not outcome.verified:
                failure = OperationResult(
                    status=ResultStatus.VERIFY_FAILED,
                    message="Configuration could not be verified.",
                    detail="\n".join(outcome.mismatches),
                    snapshot=snapshot,
                    resulting=resulting,
                    mismatches=outcome.mismatches,
                    steps=steps,
                )
                if auto_rollback:
                    return self._rollback_after_failure(failure, snapshot, step)
                return failure

            # -- CONFLICT CHECK (informational only; never fails the operation)
            conflict_text = ""
            if probe is not None:
                step("Checking for an address conflict")
                conflict = probe.result(self.CONFLICT_PROBE_TIMEOUT)
                conflict_text = conflict.describe()
                steps.append(conflict_text)
                if conflict.status is ConflictStatus.CONFLICT:
                    log.warning("Possible address conflict: %s", conflict.detail)

            self.journal.complete()
            detail_lines = [resulting.friendly_name]
            primary = resulting.primary_ipv4
            if primary is not None:
                detail_lines.append(primary.address)
                detail_lines.append(primary.subnet_mask)
            gateway = resulting.primary_gateway
            if gateway:
                detail_lines.append(gateway)
            detail_lines.extend(outcome.notes)

            log.info("Configuration applied successfully on %s", resulting.friendly_name)
            return OperationResult(
                status=ResultStatus.SUCCESS,
                message="Configuration applied successfully",
                detail="\n".join(detail_lines),
                snapshot=snapshot,
                resulting=resulting,
                steps=steps,
                conflict=conflict_text,
            )

        except IPChangerError as exc:
            log.exception("Apply failed on %s", adapter.friendly_name)
            failure = OperationResult(
                status=ResultStatus.FAILED,
                message=exc.message,
                detail=exc.detail,
                technical=exc.technical,
                snapshot=snapshot,
                steps=steps,
            )
            if auto_rollback:
                return self._rollback_after_failure(failure, snapshot, step)
            return failure
        except Exception as exc:  # never let a raw traceback reach the operator
            log.exception("Unexpected failure applying configuration")
            friendly = humanize(exc)
            failure = OperationResult(
                status=ResultStatus.FAILED,
                message=friendly.message,
                detail=friendly.detail,
                technical=friendly.technical,
                snapshot=snapshot,
                steps=steps,
            )
            if auto_rollback:
                return self._rollback_after_failure(failure, snapshot, step)
            return failure

    # --------------------------------------------------------------- waits
    def _settle(
        self, adapter: Adapter, config: IPConfiguration, step: Callable[[str], None]
    ) -> Optional[Adapter]:
        """Re-read the adapter until it shows the change, or the cap is reached.

        Returns the last state read even when it never matched: deciding what
        that means is verify_configuration's job, not this one's.
        """
        timeout = self.DHCP_SETTLE_TIMEOUT if config.is_dhcp else self.STATIC_SETTLE_TIMEOUT
        deadline = time.monotonic() + timeout
        announced = False

        while True:
            current = self.adapters.refresh(adapter)
            if current is None:
                return None
            if self._looks_applied(current, config):
                return current
            if time.monotonic() >= deadline:
                log.info(
                    "%s did not show the requested configuration within %ss",
                    adapter.friendly_name,
                    timeout,
                )
                return current
            if config.is_dhcp and not announced:
                step("Waiting for a DHCP address")
                announced = True
            time.sleep(self.SETTLE_POLL_INTERVAL)

    @staticmethod
    def _looks_applied(adapter: Adapter, config: IPConfiguration) -> bool:
        """Cheap "has Windows finished?" test for the settle loop.

        Deliberately weaker than verify_configuration, which runs immediately
        afterwards and has the last word. This only decides when to stop
        waiting, so it stays a subset and cannot disagree with the verdict.
        """
        if config.is_dhcp:
            primary = adapter.primary_ipv4
            return adapter.effective_dhcp and primary is not None and not primary.is_apipa
        # Mirrors the conditions verify_configuration treats as a mismatch, so
        # the loop never stops one moment before the verdict would pass.
        if adapter.effective_dhcp and adapter.oper_status.is_up:
            return False
        if not any(
            a.address == config.ip_address and a.prefix_length == config.prefix_length
            for a in adapter.ipv4
        ):
            return False
        return not config.gateway or config.gateway in adapter.ipv4_gateways

    # ------------------------------------------------------------ rollback
    def restore_configuration(
        self, snapshot: ConfigurationSnapshot, progress: ProgressCallback = None
    ) -> OperationResult:
        """Restore a previously captured configuration (section 14).

        This is independently executable: it needs nothing but the snapshot.
        """
        def step(text: str) -> None:
            log.info("[restore] %s", text)
            if progress:
                progress(text)

        step(f"Locating {snapshot.friendly_name}")
        adapter = self.adapters.find(guid=snapshot.guid, mac=snapshot.mac)
        if adapter is None:
            log.error("Cannot restore: adapter %s not found", snapshot.friendly_name)
            return OperationResult(
                status=ResultStatus.ROLLBACK_FAILED,
                message="The previous configuration could not be restored.",
                detail=(
                    f"The adapter '{snapshot.friendly_name}' is no longer available. "
                    "Reconnect it and use Restore again."
                ),
                snapshot=snapshot,
            )

        step("Restoring the previous configuration")
        ps_result = self.runner.run(
            "restore",
            ifIndex=adapter.if_index,
            dhcpEnabled=bool(snapshot.dhcp_enabled),
            addresses=[
                {
                    "address": a["address"],
                    "prefixLength": a["prefix_length"],
                    "subnetMask": prefix_to_mask(int(a["prefix_length"])),
                }
                for a in snapshot.addresses
            ],
            gateways=list(snapshot.gateways),
        )

        if not ps_result.ok:
            log.error("Rollback failed: %s", ps_result.error)
            return OperationResult(
                status=ResultStatus.ROLLBACK_FAILED,
                message="The previous configuration could not be restored.",
                detail=translate_error(ps_result.error, ps_result.code),
                technical=f"code={ps_result.code} error={ps_result.error}",
                snapshot=snapshot,
                steps=ps_result.steps,
            )

        expected = self._snapshot_as_configuration(snapshot)
        step("Verifying the restored configuration")
        restored = self._settle(adapter, expected, step)
        if restored is None:
            return OperationResult(
                status=ResultStatus.ROLLBACK_FAILED,
                message="The restore could not be verified.",
                detail="The adapter could not be read back after restoring.",
                snapshot=snapshot,
                steps=ps_result.steps,
            )

        outcome = verify_configuration(restored, expected)
        self.journal.complete()

        if not outcome.verified:
            log.error("Rollback verification failed: %s", outcome.describe())
            return OperationResult(
                status=ResultStatus.ROLLBACK_FAILED,
                message="The previous configuration was restored, but could not be verified.",
                detail="\n".join(outcome.mismatches),
                snapshot=snapshot,
                resulting=restored,
                mismatches=outcome.mismatches,
                steps=ps_result.steps,
            )

        log.info("Previous configuration restored on %s", restored.friendly_name)
        return OperationResult(
            status=ResultStatus.ROLLED_BACK,
            message="Previous configuration restored",
            detail=snapshot.describe(),
            snapshot=snapshot,
            resulting=restored,
            steps=ps_result.steps,
        )

    def _rollback_after_failure(
        self,
        failure: OperationResult,
        snapshot: ConfigurationSnapshot,
        step: Callable[[str], None],
    ) -> OperationResult:
        step("Rolling back to the previous configuration")
        rollback = self.restore_configuration(snapshot)
        if rollback.status is ResultStatus.ROLLED_BACK:
            failure.status = ResultStatus.ROLLED_BACK
            failure.detail = (
                f"{failure.detail}\n\nThe previous configuration has been restored."
            )
        else:
            failure.status = ResultStatus.ROLLBACK_FAILED
            failure.detail = (
                f"{failure.detail}\n\nThe rollback also failed: {rollback.detail}"
            )
        failure.steps.extend(rollback.steps)
        return failure

    @staticmethod
    def _snapshot_as_configuration(snapshot: ConfigurationSnapshot) -> IPConfiguration:
        if snapshot.dhcp_enabled:
            return IPConfiguration.dhcp()
        if not snapshot.addresses:
            return IPConfiguration(mode=ConfigMode.STATIC, ip_address="", prefix_length=24)
        first = snapshot.addresses[0]
        return IPConfiguration(
            mode=ConfigMode.STATIC,
            ip_address=str(first["address"]),
            prefix_length=int(first["prefix_length"]),
            gateway=snapshot.gateways[0] if snapshot.gateways else "",
        )

    # ------------------------------------------------------- convenience
    def set_dhcp(self, adapter: Adapter, **kwargs) -> OperationResult:
        return self.apply_configuration(adapter, IPConfiguration.dhcp(), **kwargs)

    def set_static(
        self, adapter: Adapter, config: IPConfiguration, **kwargs
    ) -> OperationResult:
        return self.apply_configuration(adapter, config, **kwargs)

    def verify_configuration(self, adapter: Adapter, expected: IPConfiguration):
        live = self.adapters.refresh(adapter)
        if live is None:
            return None
        return verify_configuration(live, expected)
