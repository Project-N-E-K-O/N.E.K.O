"""Which device organizes a character's learned memory.

Reflection synthesis, promotion, refinement, introspection, signal judging
and the slow legacy recheck run on one device per character. The published
snapshot names it; every other device still extracts facts and applies the
user's own confirmations, denials and corrections.

The holder renews its claim by publishing a new ``renewedAt``. That value
comes from the holder's clock and is only ever compared for equality: a
reader measures how long a claim has gone unrenewed on its own clock, from
the moment it first saw that claim.
"""

RENEW_INTERVAL_MS = 60 * 60 * 1000
TAKEOVER_AFTER_MS = 2 * 60 * 60 * 1000

# Desktop outranks phone, and a device running the evidence pipeline outranks
# one that is not. Zero means the device cannot organize at all.
RANK_DESKTOP_EVIDENCE, RANK_PHONE_EVIDENCE, RANK_DESKTOP, RANK_PHONE, RANK_UNABLE = 4, 3, 2, 1, 0

NONE, CLAIM, RENEW, RELEASE = 'none', 'claim', 'renew', 'release'

_gate = None


def observe(holder, seen, now):
    """Update what this device remembers of the published claim.

    seen is None or {'deviceId', 'renewedAt', 'seenAt'}; the result has the
    same shape, or is None while nobody holds the role. A local clock that
    moved backwards restarts the wait instead of making a claim look ancient.
    """
    if holder is None:
        return None
    if (seen is None or seen.get('deviceId') != holder['deviceId'] or seen.get('renewedAt') != holder['renewedAt']
            or type(seen.get('seenAt')) is not int or now < seen['seenAt']):
        return {'deviceId': holder['deviceId'], 'renewedAt': holder['renewedAt'], 'seenAt': now}
    return seen


def decide(holder, device, rank, seen, now):
    """What this device should publish about the role: nothing, a claim, a renewal or a release.

    holder is the claim in the last snapshot the server confirmed, seen the
    result of ``observe`` for it.
    """
    mine = holder is not None and holder['deviceId'] == device
    if rank <= RANK_UNABLE:
        return RELEASE if mine else NONE
    if holder is None:
        return CLAIM
    waited = now - seen['seenAt'] if seen else 0
    if mine:
        return RENEW if holder['rank'] != rank or waited >= RENEW_INTERVAL_MS else NONE
    if rank > holder['rank'] or waited >= TAKEOVER_AFTER_MS:
        return CLAIM
    return NONE


def claim(device, rank, now):
    return {'deviceId': device, 'renewedAt': now, 'rank': rank}


def set_gate(gate):
    """Install the owner's answer to "does this device organize this character?"."""
    global _gate
    _gate = gate


def allowed(name, subject=None):
    """Whether this device may run organizer-only work for a character's private memory.

    Group and member partitions never sync, so they are never gated; neither
    is anything before a gate is installed (tests, tools, a character that
    does not sync).
    """
    if subject is not None or _gate is None:
        return True
    return bool(_gate(name))
