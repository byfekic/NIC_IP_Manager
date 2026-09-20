"""The configuration transaction engine: apply, verify, rollback, dry run.

Every Windows operation is mocked. These tests assert the *decisions* the
engine makes, especially the ones that protect the operator: never applying an
invalid configuration, never assuming success, and always being able to roll
back (specification sections 13, 14, 39, 55).
"""

from __future__ import annotations

import time

from app.models.configuration import (
    ConfigurationSnapshot,
    IPConfiguration,
    ResultStatus,
)
from app.network import ip_manager as ip_manager_module
from app.network.ip_manager import NetworkManager
from app.network.verifier import (
    ConflictResult,
    ConflictStatus,
    verify_configuration,
)
from tests.conftest import FakeAdapterManager, FakeRunner, make_adapter


def build(states, runner=None, journal=None):
    """Assemble a NetworkManager with no real Windows behind it."""
    manager = NetworkManager(
        adapter_manager=FakeAdapterManager(states),
        runner=runner or FakeRunner(),
        journal=journal,
    )
    manager.STATIC_SETTLE_TIMEOUT = 0
    manager.DHCP_SETTLE_TIMEOUT = 0
    manager.SETTLE_POLL_INTERVAL = 0
    return manager


# ------------------------------------------------------------------- success
def test_successful_static_apply(journal):
    before = make_adapter()
    after = make_adapter(ipv4=(("192.168.10.50", 24),), gateways=("192.168.10.1",))
    runner = FakeRunner()
    manager = build([before, after], runner, journal)

    result = manager.apply_configuration(
        before,
        IPConfiguration.static("192.168.10.50", "255.255.255.0", "192.168.10.1"),
        check_conflict=False,
    )

    assert result.status is ResultStatus.SUCCESS
    assert result.ok
    assert "set_static" in runner.operations
    assert not journal.path.exists(), "journal must be cleared after success"


def test_successful_dhcp_apply(journal):
    before = make_adapter()
    after = make_adapter(dhcp=True, ipv4=(("10.0.0.55", 24),))
    runner = FakeRunner()
    manager = build([before, after], runner, journal)

    result = manager.apply_configuration(before, IPConfiguration.dhcp())

    assert result.status is ResultStatus.SUCCESS
    assert "set_dhcp" in runner.operations


def test_dhcp_reports_apipa_rather_than_claiming_success_silently(journal):
    """An APIPA result means no DHCP server answered; say so (section 9)."""
    before = make_adapter()
    apipa = make_adapter(dhcp=True, ipv4=(("169.254.10.10", 16),), gateways=())
    manager = build([before, apipa], FakeRunner(), journal)

    result = manager.apply_configuration(before, IPConfiguration.dhcp())

    assert result.status is ResultStatus.SUCCESS  # DHCP mode was set
    assert "APIPA" in result.detail


# ------------------------------------------------------------------ failures
def test_invalid_configuration_never_reaches_windows(journal):
    """Section 11: validation happens before any system modification."""
    before = make_adapter()
    runner = FakeRunner()
    manager = build([before], runner, journal)

    result = manager.apply_configuration(
        before,
        IPConfiguration.static("192.168.1.0", "255.255.255.0"),  # network address
        check_conflict=False,
    )

    assert result.status is ResultStatus.FAILED
    assert runner.calls == [], "no Windows call may be made for an invalid request"


def test_missing_adapter_stops_before_any_change(journal):
    """Section 41: never guess, never fall back to another adapter."""
    runner = FakeRunner()
    manager = build([None], runner, journal)

    result = manager.apply_configuration(
        make_adapter(), IPConfiguration.dhcp()
    )

    assert result.status is ResultStatus.FAILED
    assert "no longer available" in result.message
    assert "could not be found" in result.detail
    assert runner.calls == []


def test_windows_rejection_is_reported_in_plain_language(journal):
    before = make_adapter()
    runner = FakeRunner(ok=False, error="Access is denied", code="AccessDenied")
    manager = build([before, before], runner, journal)

    result = manager.apply_configuration(
        before, IPConfiguration.static("10.0.0.5", "255.255.255.0"), check_conflict=False
    )

    assert result.status is ResultStatus.FAILED
    assert "Administrator" in result.detail
    assert "CalledProcessError" not in result.detail
    assert result.can_rollback


def test_verification_failure_is_detected(journal):
    """Section 13: never assume the operation succeeded."""
    before = make_adapter()
    # Windows reported success but the adapter did not actually change.
    manager = build([before, before], FakeRunner(), journal)

    result = manager.apply_configuration(
        before,
        IPConfiguration.static("192.168.10.50", "255.255.255.0", "192.168.10.1"),
        check_conflict=False,
    )

    assert result.status is ResultStatus.VERIFY_FAILED
    assert result.mismatches
    assert result.can_rollback


def test_adapter_vanishing_mid_operation_is_handled(journal):
    before = make_adapter()
    manager = build([before, None], FakeRunner(), journal)

    result = manager.apply_configuration(
        before, IPConfiguration.static("10.0.0.5", "255.255.255.0"), check_conflict=False
    )

    assert result.status is ResultStatus.VERIFY_FAILED
    assert result.can_rollback


# ------------------------------------------------------------------ rollback
def test_rollback_restores_static_configuration(journal):
    original = make_adapter()
    manager = build([original], FakeRunner(), journal)
    snapshot = ConfigurationSnapshot.from_adapter(original)

    result = manager.restore_configuration(snapshot)

    assert result.status is ResultStatus.ROLLED_BACK


def test_rollback_restores_every_address_not_just_the_first(journal):
    """Section 14: rollback must restore the complete previous state."""
    multi = make_adapter(ipv4=(("192.168.10.100", 24), ("192.168.10.101", 24)))
    runner = FakeRunner()
    manager = build([multi], runner, journal)
    snapshot = ConfigurationSnapshot.from_adapter(multi)

    manager.restore_configuration(snapshot)

    op, params = runner.calls[0]
    assert op == "restore"
    assert len(params["addresses"]) == 2
    assert {a["address"] for a in params["addresses"]} == {
        "192.168.10.100",
        "192.168.10.101",
    }


def test_rollback_reports_failure_when_the_adapter_is_gone(journal):
    manager = build([None], FakeRunner(), journal)
    snapshot = ConfigurationSnapshot.from_adapter(make_adapter())

    result = manager.restore_configuration(snapshot)

    assert result.status is ResultStatus.ROLLBACK_FAILED


def test_rollback_failure_is_reported_clearly(journal):
    """Section 55: if rollback itself fails, say so."""
    original = make_adapter()
    manager = build([original], FakeRunner(ok=False, error="Access is denied"), journal)
    snapshot = ConfigurationSnapshot.from_adapter(original)

    result = manager.restore_configuration(snapshot)

    assert result.status is ResultStatus.ROLLBACK_FAILED


def test_auto_rollback_after_a_failed_apply(journal):
    before = make_adapter()
    runner = FakeRunner()
    manager = build([before, before], runner, journal)

    result = manager.apply_configuration(
        before,
        IPConfiguration.static("192.168.10.50", "255.255.255.0"),
        check_conflict=False,
        auto_rollback=True,
    )

    # Verification fails, so the engine rolls back to the snapshot.
    assert result.status in (ResultStatus.ROLLED_BACK, ResultStatus.ROLLBACK_FAILED)
    assert "restore" in runner.operations


# ------------------------------------------------- multiple address handling
def test_secondary_addresses_are_preserved(journal):
    """Section 32: never silently destroy *secondary* addresses."""
    multi = make_adapter(ipv4=(("192.168.10.100", 24), ("192.168.10.101", 24)))
    after = make_adapter(ipv4=(("192.168.20.5", 24), ("192.168.10.101", 24)))
    runner = FakeRunner()
    manager = build([multi, after], runner, journal)

    manager.apply_configuration(
        multi, IPConfiguration.static("192.168.20.5", "255.255.255.0"), check_conflict=False
    )

    _, params = runner.calls[0]
    preserved = {a["address"] for a in params["additional"]}
    # .101 survives; .100 was the primary being replaced.
    assert preserved == {"192.168.10.101"}


def test_changing_the_address_replaces_it_rather_than_adding_one(journal):
    """A single-address adapter must end up with only the new address."""
    single = make_adapter(ipv4=(("192.168.1.100", 24),))
    after = make_adapter(ipv4=(("192.168.1.150", 24),))
    runner = FakeRunner()
    manager = build([single, after], runner, journal)

    manager.apply_configuration(
        single, IPConfiguration.static("192.168.1.150", "255.255.255.0"), check_conflict=False
    )

    _, params = runner.calls[0]
    assert params["additional"] == [], "the old primary must not be carried over"


def test_the_privileged_layer_receives_a_subnet_mask(journal):
    """The WMI path assigns by dotted mask, so it must be supplied."""
    adapter = make_adapter()
    runner = FakeRunner()
    manager = build([adapter, adapter], runner, journal)

    manager.apply_configuration(
        adapter, IPConfiguration.static("10.0.0.5", "255.255.254.0"), check_conflict=False
    )

    _, params = runner.calls[0]
    assert params["subnetMask"] == "255.255.254.0"
    assert params["prefixLength"] == 23


def test_preserving_additional_addresses_can_be_turned_off(journal):
    multi = make_adapter(ipv4=(("192.168.10.100", 24), ("192.168.10.101", 24)))
    after = make_adapter(ipv4=(("192.168.20.5", 24),))
    runner = FakeRunner()
    manager = build([multi, after], runner, journal)

    manager.apply_configuration(
        multi,
        IPConfiguration.static("192.168.20.5", "255.255.255.0"),
        preserve_additional=False,
        check_conflict=False,
    )

    _, params = runner.calls[0]
    assert params["additional"] == []


def test_apipa_addresses_are_not_captured_in_a_snapshot():
    """Windows manages APIPA itself; restoring it explicitly is wrong."""
    adapter = make_adapter(ipv4=(("11.200.2.249", 22), ("169.254.108.212", 16)))
    snapshot = ConfigurationSnapshot.from_adapter(adapter)
    assert [a["address"] for a in snapshot.addresses] == ["11.200.2.249"]


# ------------------------------------------------------------------- journal
def test_journal_records_and_clears_an_operation(journal):
    before = make_adapter()
    after = make_adapter(ipv4=(("10.0.0.9", 24),), gateways=())
    manager = build([before, after], FakeRunner(), journal)

    assert journal.pending() is None
    manager.apply_configuration(
        before, IPConfiguration.static("10.0.0.9", "255.255.255.0"), check_conflict=False
    )
    assert journal.pending() is None


def test_journal_survives_an_interrupted_operation(journal):
    """Section 54: a crash mid-operation leaves a recoverable record."""
    before = make_adapter()
    journal.begin(
        before.friendly_name,
        before.guid,
        IPConfiguration.static("192.168.10.50", "255.255.255.0"),
        ConfigurationSnapshot.from_adapter(before),
    )

    pending = journal.pending()
    assert pending is not None
    assert pending.adapter_name == "Ethernet"
    assert "192.168.1.100" in pending.previous_description
    assert "192.168.10.50" in pending.requested_description

    journal.complete()
    assert journal.pending() is None


def test_corrupt_journal_is_discarded_not_crashed(journal):
    journal.path.write_text("{ not json", encoding="utf-8")
    assert journal.pending() is None
    assert not journal.path.exists()


# ------------------------------------------------------------------ dry run
def test_dry_run_changes_nothing(journal):
    adapter = make_adapter()
    runner = FakeRunner()
    manager = build([adapter], runner, journal)

    result = manager.dry_run(
        adapter, IPConfiguration.static("192.168.10.100", "255.255.255.0", "192.168.10.1")
    )

    assert result.status is ResultStatus.DRY_RUN
    assert runner.calls == []
    assert "192.168.1.100" in result.detail and "192.168.10.100" in result.detail


def test_dry_run_surfaces_warnings():
    adapter = make_adapter()
    manager = build([adapter])
    result = manager.dry_run(
        adapter, IPConfiguration.static("192.168.10.50", "255.255.255.0", "192.168.99.1")
    )
    assert "Warnings" in result.detail


# --------------------------------------------------------------- verification
def test_verify_detects_every_mismatch():
    adapter = make_adapter()
    outcome = verify_configuration(
        adapter, IPConfiguration.static("10.9.9.9", "255.255.0.0", "10.9.0.1")
    )
    assert not outcome.verified
    assert len(outcome.mismatches) >= 2


def test_verify_accepts_a_matching_configuration():
    adapter = make_adapter()
    outcome = verify_configuration(
        adapter, IPConfiguration.static("192.168.1.100", "255.255.255.0", "192.168.1.1")
    )
    assert outcome.verified


def test_verify_detects_wrong_subnet_mask():
    adapter = make_adapter()
    outcome = verify_configuration(
        adapter, IPConfiguration.static("192.168.1.100", "255.255.0.0")
    )
    assert not outcome.verified
    assert any("subnet mask" in m for m in outcome.mismatches)


def test_verify_dhcp_requires_dhcp_mode():
    static_adapter = make_adapter(dhcp=False)
    assert not verify_configuration(static_adapter, IPConfiguration.dhcp()).verified

    dhcp_adapter = make_adapter(dhcp=True)
    assert verify_configuration(dhcp_adapter, IPConfiguration.dhcp()).verified


# ------------------------------------------------------------------- latency
# Windows is mocked, so these assert the shape of the waiting rather than real
# durations: that the engine stops waiting as soon as the change is visible,
# and that the conflict probe overlaps the change instead of following it.
def _settling_manager(states, static_timeout=5.0):
    manager = NetworkManager(
        adapter_manager=FakeAdapterManager(states), runner=FakeRunner()
    )
    manager.STATIC_SETTLE_TIMEOUT = static_timeout
    manager.SETTLE_POLL_INTERVAL = 0.01
    return manager


def test_settle_returns_as_soon_as_the_change_is_visible():
    pending = make_adapter()
    applied = make_adapter(ipv4=(("10.0.0.5", 24),), gateways=("10.0.0.1",))
    manager = _settling_manager([pending, pending, applied])

    start = time.perf_counter()
    result = manager._settle(
        pending,
        IPConfiguration.static("10.0.0.5", "255.255.255.0", "10.0.0.1"),
        lambda _text: None,
    )
    elapsed = time.perf_counter() - start

    assert result is applied
    assert elapsed < 1.0, "waited for the cap instead of stopping at the change"


def test_settle_gives_up_at_the_cap_and_returns_what_it_last_saw():
    """A change that never appears must not hang, and must not be hidden."""
    pending = make_adapter()
    manager = _settling_manager([pending], static_timeout=0.2)

    start = time.perf_counter()
    result = manager._settle(
        pending, IPConfiguration.static("10.0.0.5", "255.255.255.0"), lambda _text: None
    )
    elapsed = time.perf_counter() - start

    # Returned rather than raised: verify_configuration decides what it means.
    assert result is pending
    assert 0.15 < elapsed < 2.0


def test_settle_waits_for_a_real_dhcp_lease_not_an_apipa_address():
    apipa = make_adapter(dhcp=True, ipv4=(("169.254.3.4", 16),), gateways=())
    leased = make_adapter(dhcp=True, ipv4=(("10.0.0.55", 24),), gateways=("10.0.0.1",))
    manager = _settling_manager([apipa, apipa, leased])
    manager.DHCP_SETTLE_TIMEOUT = 5.0

    result = manager._settle(apipa, IPConfiguration.dhcp(), lambda _text: None)

    assert result is leased


def test_the_conflict_probe_runs_alongside_the_change(monkeypatch, journal):
    """The ARP probe takes about three seconds; it must not be added on top."""
    before = make_adapter()
    after = make_adapter(ipv4=(("10.0.0.5", 24),), gateways=())

    def slow_probe(ip_address, own_macs=None):
        time.sleep(0.4)
        return ConflictResult(ConflictStatus.NO_CONFLICT)

    monkeypatch.setattr(ip_manager_module, "check_ip_conflict", slow_probe)

    class SlowRunner(FakeRunner):
        def run(self, op, timeout=None, **params):
            time.sleep(0.4)
            return super().run(op, timeout=timeout, **params)

    manager = build([before, after], SlowRunner(), journal)

    start = time.perf_counter()
    result = manager.apply_configuration(
        before, IPConfiguration.static("10.0.0.5", "255.255.255.0")
    )
    elapsed = time.perf_counter() - start

    assert result.status is ResultStatus.SUCCESS
    assert "No conflict" in result.conflict, "the probe result must still be reported"
    assert elapsed < 0.7, f"probe ran after the change, not alongside it ({elapsed:.2f}s)"


def test_a_probe_that_never_answers_does_not_block_the_result(monkeypatch, journal):
    before = make_adapter()
    after = make_adapter(ipv4=(("10.0.0.5", 24),), gateways=())

    def hanging_probe(ip_address, own_macs=None):
        time.sleep(30)

    monkeypatch.setattr(ip_manager_module, "check_ip_conflict", hanging_probe)

    manager = build([before, after], FakeRunner(), journal)
    manager.CONFLICT_PROBE_TIMEOUT = 0.1

    result = manager.apply_configuration(
        before, IPConfiguration.static("10.0.0.5", "255.255.255.0")
    )

    assert result.status is ResultStatus.SUCCESS
    assert "Unable to determine" in result.conflict
