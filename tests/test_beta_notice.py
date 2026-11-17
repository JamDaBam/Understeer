"""When to ask about a test version (beta): once per release, never over
a stable update, at most one look a day. No GTK and no network here."""
import pytest

from understeer import updates

PAGE = updates.RELEASES_URL + "/tag/v"


def beta(version):
    return {"version": version, "url": PAGE + version}


@pytest.mark.parametrize("last, now, expected", [
    (None, 1000, True),                                   # never looked
    ("garbage", 1000, True),
    (1000, 1000 + updates.BETA_CHECK_EVERY - 1, False),   # within the day,
    (1000, 1000 + 3600, False),                           # also after restarts
    (1000, 1000 + updates.BETA_CHECK_EVERY, True),
    (5000, 1000, True),                                   # the clock went back
])
def test_one_look_a_day(last, now, expected):
    assert updates.due(last, now, every=updates.BETA_CHECK_EVERY) is expected


def test_the_stable_look_keeps_its_hour():
    assert updates.CHECK_EVERY == 3600
    assert updates.due(1000, 1000 + 3600) is True
    assert updates.due(1000, 1000 + 3599) is False


@pytest.mark.parametrize("version, series", [
    ("2.4.0-beta.1", "2.4.0"), ("2.4.0-beta.2", "2.4.0"),
    ("v2.5.0-beta.10", "2.5.0"),
    ("2.4.0", None), ("2.4.0-rc1", None), ("garbage", None), (None, None),
])
def test_beta_series(version, series):
    assert updates.beta_series(version) == series


def test_checked_beta():
    assert updates.checked_beta(beta("2.4.0-beta.1")) == beta("2.4.0-beta.1")
    # only the two fields are kept
    assert updates.checked_beta({**beta("2.4.0-beta.1"), "asset": "x"}) == \
        beta("2.4.0-beta.1")


@pytest.mark.parametrize("stored", [
    None, "2.4.0-beta.1", [], {}, {"version": "2.4.0-beta.1"},
    {"version": "2.4.0-beta.1", "url": "https://evil.example/"},
    {"version": "2.4.0-beta.1", "url": PAGE + "2.4.0-beta.2"},
    {"version": "2.4.0-beta.1", "url": "http://github.com/JamDaBam/"
     "Understeer/releases/tag/v2.4.0-beta.1"},
    beta("2.4.0"), beta("2.4.0-rc1"), beta("2.4.0-beta.٢"),
    {"version": 5, "url": PAGE + "5"},
])
def test_checked_beta_refuses(stored):
    """What the settings file holds is checked again before it is used."""
    assert updates.checked_beta(stored) is None


@pytest.mark.parametrize("found, current, stable, handled, expected", [
    # a new beta, never asked about
    (beta("2.4.0-beta.1"), "2.3.3", "2.3.3", None, True),
    (beta("2.4.0-beta.1"), "2.3.3", None, None, True),
    # once per release: beta.2 after beta.1 was asked about
    (beta("2.4.0-beta.1"), "2.3.3", "2.3.3", "2.4.0", False),
    (beta("2.4.0-beta.2"), "2.3.3", "2.3.3", "2.4.0", False),
    (beta("2.4.0-beta.10"), "2.3.3", None, "2.4.0", False),
    # a newer release may be asked about once again
    (beta("2.5.0-beta.1"), "2.4.0", "2.4.0", "2.4.0", True),
    (beta("2.5.0-beta.1"), "2.3.3", None, "2.4.0", True),
    # an older release asked about doesn't hide a newer one; a newer does
    (beta("2.4.0-beta.1"), "2.3.3", None, "2.3.0", True),
    (beta("2.4.0-beta.1"), "2.3.3", None, "2.5.0", False),
    # what was asked about is unreadable: ask (once more)
    (beta("2.4.0-beta.1"), "2.3.3", None, "garbage", True),
    (beta("2.4.0-beta.1"), "2.3.3", None, 5, True),
    # a stable update comes first, and a stable release ends its betas
    (beta("2.4.0-beta.1"), "2.3.3", "2.3.4", None, False),
    (beta("2.4.0-beta.1"), "2.3.3", "2.4.0", None, False),
    (beta("2.4.0-beta.1"), "2.3.3", "2.4.1", None, False),
    (beta("2.4.0-beta.1"), "2.3.3", "2.3.2", None, True),   # older: no matter
    (beta("2.4.0-beta.1"), "2.3.3", "2.5.0-beta.1", None, True),  # no stable
    (beta("2.4.0-beta.1"), "2.3.3", "garbage", None, True),
    # only newer than the running version
    (beta("2.4.0-beta.1"), "2.4.0-beta.1", None, None, False),
    (beta("2.4.0-beta.1"), "2.4.0-beta.2", None, None, False),
    (beta("2.4.0-beta.1"), "2.4.0", None, None, False),
    # a beta user isn't asked about another beta of the same release
    (beta("2.4.0-beta.2"), "2.4.0-beta.1", None, None, False),
    (beta("2.4.0-beta.10"), "2.4.0-beta.9", "2.3.3", None, False),
    (beta("2.5.0-beta.1"), "2.4.0-beta.1", None, None, True),
    (beta("2.5.0-beta.1"), "2.4.0-beta.1", None, "2.4.0", True),
    (beta("2.4.0-beta.1"), "2.3.3-4-gabc", None, None, False),  # from git
    (beta("2.4.0-beta.1"), None, None, None, False),
    # broken betas
    (None, "2.3.3", None, None, False),
    ({"version": "2.4.0-beta.1", "url": "https://evil.example/"}, "2.3.3",
     None, None, False),
    (beta("2.4.0"), "2.3.3", None, None, False),
])
def test_should_offer_beta(found, current, stable, handled, expected):
    assert updates.should_offer_beta(found, current, stable, handled) is expected


def test_a_beta_never_reaches_the_stable_banner():
    """The banner shows update_latest only if is_newer; a beta never is,
    while a beta user is offered the stable release."""
    assert not updates.is_newer("2.4.0-beta.1", "2.3.3")
    assert updates.is_newer("2.4.0", "2.4.0-beta.2")


def test_no_hardware_here():
    assert not {"device", "inputs", "leds", "display", "capabilities"} \
        & set(vars(updates))


@pytest.mark.parametrize("found, current, expected", [
    ("2.4.0-beta.2", "2.4.0-beta.1", False),    # same release: no invitation
    ("2.5.0-beta.1", "2.4.0-beta.1", True),     # a newer release: one
    ("2.4.0-beta.1", "2.3.3", True),            # a stable user who opted in
    ("2.4.0-beta.10", "2.4.0", False),          # the release is out already
])
def test_policy_for_beta_testers(found, current, expected):
    assert updates.should_offer_beta(beta(found), current, None, None) \
        is expected


def test_a_running_beta_marks_nothing_as_asked():
    """Not asking about 2.4.0-beta.2 while running 2.4.0-beta.1 is decided
    from the running version alone, so the next release still counts."""
    assert not updates.should_offer_beta(beta("2.4.0-beta.2"), "2.4.0-beta.1",
                                         None, None)
    assert updates.should_offer_beta(beta("2.5.0-beta.1"), "2.4.0-beta.1",
                                     None, None)


@pytest.mark.parametrize("build", [1, 2, 9, 10, 99])
def test_a_handled_release_stays_handled_for_every_build(build):
    assert not updates.should_offer_beta(beta(f"2.4.0-beta.{build}"), "2.3.3",
                                         None, "2.4.0")
