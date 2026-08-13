"""The SSRF guard: nothing is fetched until DNS has been resolved and every answer cleared.

The point of these tests is not that "private ranges are blocked" but that the block is
decided on the *addresses a name actually resolves to*, re-checked on every resolution, and
that the caller is handed the pinned address it must connect to.
"""

from __future__ import annotations

import pytest

from app.ingestion.web.safety import (
    AddressGuard,
    BlockReason,
    NetworkPolicy,
    SsrfBlocked,
    classify_address,
)
from app.ingestion.web.urls import normalize_url

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def static_resolver(*addresses: str):
    def resolve(host: str, port: int) -> tuple[str, ...]:
        return addresses

    return resolve


def guard_for(*addresses: str, policy: NetworkPolicy | None = None) -> AddressGuard:
    return AddressGuard(policy=policy or NetworkPolicy(), resolver=static_resolver(*addresses))


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("127.0.0.1", BlockReason.LOOPBACK),
        ("127.1.2.3", BlockReason.LOOPBACK),
        ("::1", BlockReason.LOOPBACK),
        ("10.0.0.5", BlockReason.PRIVATE),
        ("172.16.4.1", BlockReason.PRIVATE),
        ("192.168.1.1", BlockReason.PRIVATE),
        ("169.254.1.1", BlockReason.LINK_LOCAL),
        ("169.254.169.254", BlockReason.CLOUD_METADATA),
        ("169.254.170.2", BlockReason.CLOUD_METADATA),
        ("100.64.0.1", BlockReason.CARRIER_GRADE_NAT),
        ("100.100.100.200", BlockReason.CLOUD_METADATA),
        ("fd00::1", BlockReason.UNIQUE_LOCAL),
        ("fd00:ec2::254", BlockReason.CLOUD_METADATA),
        ("fe80::1", BlockReason.LINK_LOCAL),
        ("0.0.0.0", BlockReason.UNSPECIFIED),  # noqa: S104 - test input, not a bind
        ("::", BlockReason.UNSPECIFIED),
        ("224.0.0.1", BlockReason.MULTICAST),
        ("255.255.255.255", BlockReason.RESERVED),
        ("240.0.0.1", BlockReason.RESERVED),
    ],
)
def test_every_address_that_can_reach_the_deployment_itself_is_classified(
    address: str, reason: BlockReason
) -> None:
    assert classify_address(address) is reason


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "8.8.8.8"])
def test_a_globally_routable_address_is_not_blocked(address: str) -> None:
    assert classify_address(address) is None


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "0:0:0:0:0:ffff:a00:1",
    ],
)
def test_an_ipv4_address_wrapped_in_ipv6_is_unwrapped_before_it_is_judged(address: str) -> None:
    """``::ffff:127.0.0.1`` is loopback wearing a different notation."""
    assert classify_address(address) is not None


@pytest.mark.parametrize("address", ["64:ff9b::7f00:1", "2002:7f00:1::", "2001::1"])
def test_transition_notations_that_embed_a_blocked_v4_address_are_blocked(address: str) -> None:
    assert classify_address(address) is not None


def test_a_url_resolving_to_a_public_address_is_allowed_and_returns_the_pinned_address() -> None:
    target = guard_for(PUBLIC_V4).check_url(normalize_url("https://docs.example.test/guide"))

    assert target.addresses == (PUBLIC_V4,)
    assert target.url.host == "docs.example.test"
    assert str(target.connect_url) == f"https://{PUBLIC_V4}/guide"


def test_a_url_resolving_to_a_private_address_is_refused_with_the_reason() -> None:
    with pytest.raises(SsrfBlocked) as caught:
        guard_for("10.1.2.3").check_url(normalize_url("https://internal.example.test/"))

    assert caught.value.reason is BlockReason.PRIVATE
    assert "10.1.2.3" in str(caught.value)


def test_one_bad_answer_poisons_the_whole_name() -> None:
    """A round-robin record mixing a public and a private answer must not be usable.

    Allowing the public answer would let an attacker win the race on any later resolution.
    """
    with pytest.raises(SsrfBlocked) as caught:
        guard_for(PUBLIC_V4, "127.0.0.1").check_url(normalize_url("https://rebind.example.test/"))

    assert caught.value.reason is BlockReason.LOOPBACK


def test_a_host_that_does_not_resolve_is_refused() -> None:
    with pytest.raises(SsrfBlocked) as caught:
        guard_for().check_url(normalize_url("https://nowhere.example.test/"))

    assert caught.value.reason is BlockReason.NO_ADDRESS


def test_a_resolver_failure_is_reported_as_a_block_not_a_socket_error() -> None:
    def failing(host: str, port: int) -> tuple[str, ...]:
        raise OSError("nodename nor servname provided")

    with pytest.raises(SsrfBlocked) as caught:
        AddressGuard(resolver=failing).check_url(normalize_url("https://nowhere.example.test/"))

    assert caught.value.reason is BlockReason.NO_ADDRESS


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "app.localhost",
        "printer.local",
        "metadata.google.internal",
        "db.internal",
        "gateway.home.arpa",
        "intranet",
    ],
)
def test_names_that_only_mean_something_inside_the_network_never_reach_dns(host: str) -> None:
    resolutions: list[str] = []

    def recording(name: str, port: int) -> tuple[str, ...]:
        resolutions.append(name)
        return (PUBLIC_V4,)

    guard = AddressGuard(resolver=recording)
    with pytest.raises(SsrfBlocked) as caught:
        guard.check_url(normalize_url(f"https://{host}/"))

    assert caught.value.reason is BlockReason.HOSTNAME
    assert resolutions == []


def test_an_ip_literal_in_the_url_is_judged_directly_and_never_resolved() -> None:
    resolutions: list[str] = []

    def recording(name: str, port: int) -> tuple[str, ...]:
        resolutions.append(name)
        return (PUBLIC_V4,)

    guard = AddressGuard(resolver=recording)
    with pytest.raises(SsrfBlocked) as caught:
        guard.check_url(normalize_url("http://169.254.169.254/latest/meta-data/"))

    assert caught.value.reason is BlockReason.CLOUD_METADATA
    assert resolutions == []


def test_a_public_ip_literal_is_allowed_without_a_lookup() -> None:
    target = guard_for().check_url(normalize_url(f"https://{PUBLIC_V4}/a"))

    assert target.addresses == (PUBLIC_V4,)


@pytest.mark.parametrize("port", [22, 25, 3306, 6379, 8000, 11211])
def test_ports_outside_the_allow_list_are_refused(port: int) -> None:
    with pytest.raises(SsrfBlocked) as caught:
        guard_for(PUBLIC_V4).check_url(normalize_url(f"http://docs.example.test:{port}/"))

    assert caught.value.reason is BlockReason.PORT


def test_an_operator_may_widen_the_port_allow_list_explicitly() -> None:
    policy = NetworkPolicy(allowed_ports=frozenset({80, 443, 8443}))

    target = guard_for(PUBLIC_V4, policy=policy).check_url(
        normalize_url("https://docs.example.test:8443/")
    )

    assert target.url.port == 8443


def test_the_guard_re_resolves_on_every_call_so_a_stale_verdict_is_never_reused() -> None:
    """DNS rebinding in its simplest form: the same name answers differently over time."""
    answers = iter([(PUBLIC_V4,), ("127.0.0.1",)])

    def rebinding(host: str, port: int) -> tuple[str, ...]:
        return next(answers)

    guard = AddressGuard(resolver=rebinding)
    url = normalize_url("https://rebind.example.test/")

    assert guard.check_url(url).addresses == (PUBLIC_V4,)
    with pytest.raises(SsrfBlocked) as caught:
        guard.check_url(url)
    assert caught.value.reason is BlockReason.LOOPBACK


def test_the_guard_caps_how_many_addresses_it_will_consider() -> None:
    policy = NetworkPolicy(max_addresses=2)
    addresses = tuple(f"93.184.216.{index}" for index in range(1, 10))

    target = guard_for(*addresses, policy=policy).check_url(
        normalize_url("https://docs.example.test/")
    )

    assert len(target.addresses) == 2


def test_a_policy_that_allows_private_addresses_must_be_asked_for_explicitly() -> None:
    """Only a test harness or an operator pointing at an intranet mirror may do this."""
    policy = NetworkPolicy(allow_private_addresses=True)

    target = guard_for("127.0.0.1", policy=policy).check_url(
        normalize_url("http://stub.example.test/")
    )

    assert target.addresses == ("127.0.0.1",)


def test_the_default_policy_is_the_safe_one() -> None:
    policy = NetworkPolicy()

    assert policy.allow_private_addresses is False
    assert policy.allowed_schemes == frozenset({"http", "https"})
    assert policy.allowed_ports == frozenset({80, 443})
