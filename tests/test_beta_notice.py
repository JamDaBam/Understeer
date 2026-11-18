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
    # a beta user is told about a newer beta of the same release too
    (beta("2.4.0-beta.2"), "2.4.0-beta.1", None, None, True),
    (beta("2.4.0-beta.10"), "2.4.0-beta.9", "2.3.3", None, True),
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
    ("2.4.0-beta.2", "2.4.0-beta.1", True),     # same release: newer beta
    ("2.5.0-beta.1", "2.4.0-beta.1", True),     # a newer release: one
    ("2.4.0-beta.1", "2.3.3", True),            # a stable user who opted in
    ("2.4.0-beta.10", "2.4.0", False),          # the release is out already
])
def test_policy_for_beta_testers(found, current, expected):
    assert updates.should_offer_beta(beta(found), current, None, None) \
        is expected


def test_a_running_beta_isnt_held_back_by_the_release_asked_about():
    """A beta user is told about newer betas also of a release whose
    invitation was answered (handled); only notified counts."""
    assert updates.should_offer_beta(beta("2.4.0-beta.2"), "2.4.0-beta.1",
                                     None, "2.4.0")
    assert updates.should_offer_beta(beta("2.5.0-beta.1"), "2.4.0-beta.1",
                                     None, "2.5.0")


@pytest.mark.parametrize("build", [1, 2, 9, 10, 99])
def test_a_handled_release_stays_handled_for_every_build(build):
    assert not updates.should_offer_beta(beta(f"2.4.0-beta.{build}"), "2.3.3",
                                         None, "2.4.0")


# -- a beta user: each newer test version once (beta_notified) --------------
@pytest.mark.parametrize("found, current, stable, notified, expected", [
    # newer in the same release, by number: beta.10 after beta.9
    ("2.4.0-beta.2", "2.4.0-beta.1", None, None, True),
    ("2.4.0-beta.10", "2.4.0-beta.9", None, None, True),
    ("2.4.0-beta.9", "2.4.0-beta.10", None, None, False),
    ("2.4.0-beta.10", "2.4.0-beta.2", None, "2.4.0-beta.9", True),
    # a newer release
    ("2.5.0-beta.1", "2.4.0-beta.2", None, None, True),
    ("2.5.0-beta.1", "2.4.0-beta.2", None, "2.4.0-beta.3", True),
    # never the running one or an older one
    ("2.4.0-beta.2", "2.4.0-beta.2", None, None, False),
    ("2.4.0-beta.1", "2.4.0-beta.2", None, None, False),
    ("2.3.0-beta.5", "2.4.0-beta.1", None, None, False),
    # told about already: the same or a newer one
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "2.4.0-beta.3", False),
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "2.4.0-beta.4", False),
    ("2.4.0-beta.3", "2.4.0-beta.1", None, "2.5.0-beta.1", False),
    # told about an older one: the newer one counts
    ("2.4.0-beta.3", "2.4.0-beta.1", None, "2.4.0-beta.2", True),
    # after going back to an older beta, nothing told about is repeated
    ("2.4.0-beta.3", "2.4.0-beta.1", None, "2.4.0-beta.3", False),
    # what was told about is unreadable: as if nothing was (ask once)
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "garbage", True),
    ("2.4.0-beta.3", "2.4.0-beta.2", None, 5, True),
    ("2.4.0-beta.3", "2.4.0-beta.2", None, ["2.4.0-beta.3"], True),
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "v2.4.0-beta.3", True),
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "2.4.0", True),   # no beta
    ("2.4.0-beta.3", "2.4.0-beta.2", None, "9.0.0", True),   # no beta
    # a stable update comes first, and a stable release ends its betas
    ("2.4.0-beta.3", "2.4.0-beta.2", "2.4.0", None, False),
    ("2.5.0-beta.1", "2.4.0-beta.2", "2.4.0", None, False),
    ("2.5.0-beta.1", "2.4.0-beta.2", "2.5.0", None, False),
    ("2.4.0-beta.3", "2.4.0-beta.2", "2.3.3", None, True),   # older: no matter
    ("2.4.0-beta.3", "2.4.0-beta.2", "2.4.0-beta.9", None, True),  # no stable
])
def test_a_beta_user_hears_of_each_newer_beta_once(found, current, stable,
                                                   notified, expected):
    assert updates.should_offer_beta(beta(found), current, stable, None,
                                     notified) is expected


@pytest.mark.parametrize("handled", [None, "2.4.0", "2.5.0", "garbage"])
def test_a_beta_user_ignores_the_release_asked_about(handled):
    assert updates.should_offer_beta(beta("2.4.0-beta.3"), "2.4.0-beta.2",
                                     None, handled, None)


@pytest.mark.parametrize("notified", [None, "2.4.0-beta.1", "2.9.0-beta.9"])
def test_a_stable_user_keeps_the_invitation_once_per_release(notified):
    """beta_notified changes nothing for a stable version: handled does."""
    offer = updates.should_offer_beta
    assert offer(beta("2.4.0-beta.1"), "2.3.3", None, None, notified)
    assert not offer(beta("2.4.0-beta.2"), "2.3.3", None, "2.4.0", notified)
    assert not offer(beta("2.4.0-beta.10"), "2.3.3", None, "2.4.0", notified)
    assert offer(beta("2.5.0-beta.1"), "2.3.3", None, "2.4.0", notified)


@pytest.mark.parametrize("stored, expected", [
    ("2.4.0-beta.2", "2.4.0-beta.2"), ("10.0.0-beta.10", "10.0.0-beta.10"),
    (None, None), (5, None), (["2.4.0-beta.2"], None), ("", None),
    ("2.4.0", None), ("v2.4.0-beta.2", None), ("2.4.0-beta.2 ", None),
    ("2.4.0-rc1", None), ("2.4.0-beta.x", None), ("٢.4.0-beta.1", None),
])
def test_notified_beta(stored, expected):
    assert updates.notified_beta(stored) == expected


@pytest.mark.parametrize("stored, expected", [
    ("2.4.0", "2.4.0"), ("10.0", "10.0"), (None, None), (5, None),
    ("2.4.0-beta.1", None), ("garbage", None), ("", None),
])
def test_handled_series(stored, expected):
    assert updates.handled_series(stored) == expected


@pytest.mark.parametrize("versions, expected", [
    (("2.4.0-beta.9", "2.4.0-beta.10"), "2.4.0-beta.10"),     # by number
    (("2.4.0-beta.10", "2.4.0-beta.9"), "2.4.0-beta.10"),
    (("2.5.0-beta.1", "2.4.0-beta.10"), "2.5.0-beta.1"),
    (("2.4.0-beta.2", None, "garbage", 5), "2.4.0-beta.2"),
    (("2.4.0", "2.4.0-beta.1"), "2.4.0-beta.1"),              # no beta: left out
    ((None, "garbage"), None),
    ((), None),
])
def test_newest_beta_kept(versions, expected):
    """What was told about never goes back to an older beta."""
    assert updates.newest(versions, updates.notified_beta) == expected


@pytest.mark.parametrize("versions, expected", [
    (("2.4.0", "2.10.0"), "2.10.0"), (("2.10.0", "2.4.0"), "2.10.0"),
    (("2.4.0", "garbage", None, "2.5.0-beta.1"), "2.4.0"),
    ((None,), None),
])
def test_newest_release_kept(versions, expected):
    assert updates.newest(versions, updates.handled_series) == expected


def test_a_beta_user_through_the_releases():
    """Running 2.4.0-beta.1 and answering each question: every newer beta
    is told about once, then 2.4.0 itself comes as a stable update."""
    offer, notified = updates.should_offer_beta, None
    told = []
    for running, found in [("2.4.0-beta.1", "2.4.0-beta.2"),
                           ("2.4.0-beta.1", "2.4.0-beta.2"),     # again
                           ("2.4.0-beta.2", "2.4.0-beta.2"),     # installed
                           ("2.4.0-beta.2", "2.4.0-beta.3"),
                           ("2.4.0-beta.2", "2.4.0-beta.10"),
                           ("2.4.0-beta.2", "2.4.0-beta.9"),     # older
                           ("2.4.0-beta.10", "2.5.0-beta.1")]:
        if offer(beta(found), running, None, None, notified):
            told.append(found)
            notified = updates.newest((found, notified), updates.notified_beta)
    assert told == ["2.4.0-beta.2", "2.4.0-beta.3", "2.4.0-beta.10",
                    "2.5.0-beta.1"]
    assert not offer(beta("2.5.0-beta.2"), "2.4.0-beta.10", "2.4.0", None,
                     notified)              # the stable update first
    assert updates.is_newer("2.4.0", "2.4.0-beta.10")
