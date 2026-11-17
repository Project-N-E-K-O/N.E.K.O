"""Write tests/fixtures/long_term_merge_cases.json.

Every expected document below is written out by hand. The file is the shared
contract for each client's three-way merge, so nothing here calls the merge.
Run from the repository root after editing: python scripts/make_long_term_merge_cases.py
"""

import json
from pathlib import Path

COMPLETED = 9000
A, B, C, D = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32
DESKTOP, PHONE = '1' * 32, '2' * 32


def fact(identity, text='likes tea', **metadata):
    return {'kind': 'fact', 'subject': 'master', 'id': identity, 'text': text,
            'metadata': {'signalJudged': True, **metadata}}


def reflection(identity, text='enjoys quiet evenings', **metadata):
    return {'kind': 'reflection', 'subject': 'relationship', 'id': identity, 'text': text,
            'metadata': {'status': 'pending', **metadata}}


def document(entries=(), *, retirements=(), confirmations=(), a=(), b=(), organizer=None, maintenance=None,
             lineage=None, cold=None, completed=1000):
    value = {'schemaVersion': 3, 'completedAt': completed, 'entries': list(entries),
             'evidence': {'retirements': list(retirements), 'confirmations': list(confirmations)},
             'progress': {'a': list(a), 'b': list(b)}}
    if organizer is not None:
        value['organizer'] = organizer
    if maintenance is not None:
        value['maintenance'] = maintenance
    if lineage is not None:
        value['lineage'] = lineage
    if cold is not None:
        value['cold'] = list(cold)
    return value


def older(entries, *, retirements=(), completed=1000):
    return {'schemaVersion': 2, 'completedAt': completed, 'entries': list(entries),
            'evidence': {'retirements': list(retirements), 'confirmations': []}}


def based(entries=(), native=None, **parts):
    return {'document': document(entries, **parts), 'nativeEntries': list(entries if native is None else native)}


def expected(entries=(), **parts):
    return document(entries, completed=COMPLETED, **parts)


def claim(device, renewed, rank):
    return {'deviceId': device, 'renewedAt': renewed, 'rank': rank}


def listed(identity, kind='fact', status=None, at=500, **fields):
    """A cold-list item: identity and state only, never text."""
    subject = 'relationship' if kind == 'reflection' else 'master'
    return {'kind': kind, 'subject': subject, 'id': identity,
            'status': status or ('archived' if kind != 'fact' else 'retracted'), 'archivedAt': at, **fields}


def retirement(identity, sources, **fields):
    return {'id': identity, 'subject': 'master', 'text': f'retired {identity}', 'sourceMessageIds': list(sources), **fields}


CASES = []


def case(name, base, local, remote, result):
    CASES.append({'name': name, 'base': base, 'local': local, 'remote': remote,
                  'completedAt': COMPLETED, 'expected': result})


shared = fact('f1')

case('remote edit reaches an untouched entry',
     based([shared]), document([shared]), document([fact('f1', 'likes green tea')]),
     expected([fact('f1', 'likes green tea')]))

case('remote deletion reaches an untouched entry',
     based([shared, fact('f2')]), document([shared, fact('f2')]), document([shared]),
     expected([shared]))

case('local edit survives an unchanged remote',
     based([shared]), document([fact('f1', 'likes green tea')]), document([shared]),
     expected([fact('f1', 'likes green tea')]))

case('local deletion survives an unchanged remote',
     based([shared, fact('f2')]), document([shared]), document([shared, fact('f2')]),
     expected([shared]))

case('remote edit wins over a local deletion',
     based([shared, fact('f2')]), document([shared]), document([shared, fact('f2', 'likes black tea')]),
     expected([shared, fact('f2', 'likes black tea')]))

case('remote deletion wins over a local edit',
     based([shared, fact('f2')]), document([shared, fact('f2', importance=9)]), document([shared]),
     expected([shared]))

case('a confirmation made here outlives a remote deletion',
     based([reflection('r1', status='confirmed', userConfirmCount=0)]),
     document([reflection('r1', status='confirmed', userConfirmCount=1)]),
     document([]),
     expected([reflection('r1', status='confirmed', userConfirmCount=1)]))

case('a correction made here outlives a remote deletion',
     based([reflection('r1')]),
     document([reflection('r1', 'enjoys loud evenings', versionHistory=[
         {'text': 'enjoys quiet evenings', 'replacedAt': 2000, 'reason': 'correction'}])]),
     document([]),
     expected([reflection('r1', 'enjoys loud evenings', versionHistory=[
         {'text': 'enjoys quiet evenings', 'replacedAt': 2000, 'reason': 'correction'}])]))

five = [{'text': f'wording {index}', 'replacedAt': 1000 + index, 'reason': 'correction'} for index in range(5)]
case('a sixth correction is new even though the history still holds five',
     based([reflection('r1', 'wording 5', versionHistory=five)]),
     document([reflection('r1', 'wording 6', versionHistory=[
         *five[1:], {'text': 'wording 5', 'replacedAt': 2000, 'reason': 'correction'}])]),
     document([]),
     expected([reflection('r1', 'wording 6', versionHistory=[
         *five[1:], {'text': 'wording 5', 'replacedAt': 2000, 'reason': 'correction'}])]))

case('a fact only this device has is kept and retires nothing',
     based([shared]), document([shared, fact('new', 'has a cat', signalJudged=False)]), document([shared]),
     expected([shared, fact('new', 'has a cat', signalJudged=False)]))

case('a fact only the remote has is taken',
     based([shared]), document([shared]), document([shared, fact('new', 'has a cat')]),
     expected([shared, fact('new', 'has a cat')]))

case('without a base both sides are kept',
     None, document([fact('here', 'has a cat')]), document([fact('there', 'has a dog')]),
     expected([fact('here', 'has a cat'), fact('there', 'has a dog')]))

case('without a base a shared entry merges field by field',
     None,
     document([fact('f1', 'likes tea', importance=7, absorbed=True, signalJudged=False,
                    sourceMessageIds=['sync:' + A], reinforcement=1)]),
     document([fact('f1', 'likes green tea', importance=4, absorbed=False, signalJudged=True,
                    sourceMessageIds=['sync:' + B], reinforcement=3)]),
     expected([fact('f1', 'likes green tea', importance=7, absorbed=True, signalJudged=True,
                    sourceMessageIds=['sync:' + B, 'sync:' + A], reinforcement=3)]))

case('a fact retired elsewhere does not come back from a device without a base',
     None,
     document([fact('gone', 'lives in Paris'), fact('kept', 'has a cat')]),
     document([], retirements=[retirement('gone', ['sync:' + A])]),
     expected([fact('kept', 'has a cat')], retirements=[retirement('gone', ['sync:' + A])]))

case('flags that only turn true are joined',
     based([fact('f1', absorbed=False, signalJudged=False, importance=5)]),
     document([fact('f1', absorbed=True, signalJudged=False, importance=6)]),
     document([fact('f1', absorbed=False, signalJudged=True, importance=7)]),
     expected([fact('f1', absorbed=True, signalJudged=True, importance=8)]))

case('the later status and its wording win',
     based([reflection('r1', 'draft', status='pending')]),
     document([reflection('r1', 'confirmed here', status='confirmed', confirmedAt=3000)]),
     document([reflection('r1', 'promoted there', status='promoted', promotedAt=4000)]),
     expected([reflection('r1', 'promoted there', status='promoted', confirmedAt=3000, promotedAt=4000)]))

case('a denial here wins over a promotion there',
     based([reflection('r1', 'draft', status='confirmed')]),
     document([reflection('r1', 'denied here', status='denied', voidedAt=5000)]),
     document([reflection('r1', 'promoted there', status='promoted', promotedAt=4000)]),
     expected([reflection('r1', 'denied here', status='denied', voidedAt=5000, promotedAt=4000)]))

case('equal status keeps the published wording',
     based([reflection('r1', 'draft')]),
     document([reflection('r1', 'reworded here')]),
     document([reflection('r1', 'reworded there')]),
     expected([reflection('r1', 'reworded there')]))

case('counters add both increments',
     based([reflection('r1', status='confirmed', reinforcement=2, disputation=1, userConfirmCount=1,
                       userFactReinforceCount=3)]),
     document([reflection('r1', status='confirmed', reinforcement=3, disputation=1, userConfirmCount=2,
                          userFactReinforceCount=4)]),
     document([reflection('r1', status='confirmed', reinforcement=5, disputation=2, userConfirmCount=2,
                          userFactReinforceCount=3)]),
     expected([reflection('r1', status='confirmed', reinforcement=6, disputation=2, userConfirmCount=3,
                          userFactReinforceCount=4)]))

case('importance stays within one to ten',
     based([fact('f1', importance=8)]), document([fact('f1', importance=10)]), document([fact('f1', importance=10)]),
     expected([fact('f1', importance=10)]))

case('a counter the base lacked starts from zero',
     based([reflection('r1', status='confirmed')]),
     document([reflection('r1', status='confirmed', reinforcement=1)]),
     document([reflection('r1', status='confirmed', reinforcement=2)]),
     expected([reflection('r1', status='confirmed', reinforcement=3)]))

case('a day both devices counted is one day',
     based([reflection('r1', status='confirmed', subZeroDays=1, subZeroLastIncrementDate='2026-11-09')]),
     document([reflection('r1', status='confirmed', subZeroDays=2, subZeroLastIncrementDate='2026-11-10')]),
     document([reflection('r1', status='confirmed', subZeroDays=2, subZeroLastIncrementDate='2026-11-10')]),
     expected([reflection('r1', status='confirmed', subZeroDays=2, subZeroLastIncrementDate='2026-11-10')]))

case('days counted on different dates keep the larger count and later date',
     based([reflection('r1', status='confirmed', subZeroDays=1, subZeroLastIncrementDate='2026-11-08')]),
     document([reflection('r1', status='confirmed', subZeroDays=2, subZeroLastIncrementDate='2026-11-09')]),
     document([reflection('r1', status='confirmed', subZeroDays=3, subZeroLastIncrementDate='2026-11-11')]),
     expected([reflection('r1', status='confirmed', subZeroDays=3, subZeroLastIncrementDate='2026-11-11')]))

case('a larger day count on this device is kept over a smaller published one',
     based([reflection('r1', status='confirmed', subZeroDays=1, subZeroLastIncrementDate='2026-11-08')]),
     document([reflection('r1', status='confirmed', subZeroDays=3, subZeroLastIncrementDate='2026-11-11')]),
     document([reflection('r1', status='confirmed', subZeroDays=2, subZeroLastIncrementDate='2026-11-09')]),
     expected([reflection('r1', status='confirmed', subZeroDays=3, subZeroLastIncrementDate='2026-11-11')]))

case('last-signal times keep the later one',
     based([reflection('r1', status='confirmed', reinLastSignalAt=100, updatedAt=100)]),
     document([reflection('r1', status='confirmed', reinLastSignalAt=300, updatedAt=300)]),
     document([reflection('r1', status='confirmed', reinLastSignalAt=200, updatedAt=400)]),
     expected([reflection('r1', status='confirmed', reinLastSignalAt=300, updatedAt=400)]))

case('identifier lists join and honour removals',
     based([reflection('r1', sourceFactIds=['f1', 'f2', 'f3'])]),
     document([reflection('r1', sourceFactIds=['f1', 'f3', 'f5'])]),
     document([reflection('r1', sourceFactIds=['f1', 'f2', 'f4'])]),
     expected([reflection('r1', sourceFactIds=['f1', 'f4', 'f5'])]))

case('an entry this device exports differently is not a local edit',
     based([fact('f1')], native=[fact('f1', sourceSyncId='desktop:f1', createdAt=0, updatedAt=0)]),
     document([fact('f1', sourceSyncId='desktop:f1', createdAt=0, updatedAt=0)]),
     document([fact('f1', 'likes green tea')]),
     expected([fact('f1', 'likes green tea')]))

case('an entry this device exports differently keeps the published form when nothing changed',
     based([fact('f1')], native=[fact('f1', sourceSyncId='desktop:f1', createdAt=0, updatedAt=0)]),
     document([fact('f1', sourceSyncId='desktop:f1', createdAt=0, updatedAt=0)]),
     document([fact('f1')]),
     expected([fact('f1')]))

case('a local field change is carried onto the published form',
     based([fact('f1', importance=5)], native=[fact('f1', importance=5, sourceSyncId='desktop:f1')]),
     document([fact('f1', importance=5, absorbed=True, sourceSyncId='desktop:f1')]),
     document([fact('f1', 'likes green tea', importance=5)]),
     expected([fact('f1', 'likes green tea', importance=5, absorbed=True)]))

case('progress keeps the published order and appends what this device added',
     based(a=[A, B], b=[A]), document(a=[A, B, D], b=[A]), document(a=[A, B, C], b=[A, B]),
     expected(a=[A, B, C, D], b=[A, B]))

case('progress the publisher already dropped is not added back',
     based(a=[A, B, C]), document(a=[A, B, C, D]), document(a=[C]),
     expected(a=[C, D]))

case('a published organizer replaces an untouched one',
     based(organizer=claim(DESKTOP, 100, 2)), document(organizer=claim(DESKTOP, 100, 2)),
     document(organizer=claim(DESKTOP, 200, 2)),
     expected(organizer=claim(DESKTOP, 200, 2)))

case('a higher-ranked claim made here is kept',
     based(organizer=claim(PHONE, 100, 1)), document(organizer=claim(DESKTOP, 500, 4)),
     document(organizer=claim(PHONE, 200, 1)),
     expected(organizer=claim(DESKTOP, 500, 4)))

case('an equal-ranked claim made here yields to the published one',
     based(), document(organizer=claim(DESKTOP, 500, 2)), document(organizer=claim(PHONE, 400, 2)),
     expected(organizer=claim(PHONE, 400, 2)))

case('giving up the organizer role is published',
     based(organizer=claim(DESKTOP, 100, 2)), document(), document(organizer=claim(DESKTOP, 100, 2)),
     expected())

case('a claim made here replaces a role given up there',
     based(organizer=claim(PHONE, 100, 1)), document(organizer=claim(DESKTOP, 500, 2)), document(),
     expected(organizer=claim(DESKTOP, 500, 2)))

case('one-off maintenance marks and lineage keep the larger value',
     based(maintenance={'legacyDedup': 1}),
     document(maintenance={'legacyDedup': 1, 'other': 2}),
     document(maintenance={'legacyDedup': 2}),
     expected(maintenance={'legacyDedup': 2, 'other': 2}))

case('a remote written without reading the base proves no deletion',
     based([shared, fact('f2')]), document([shared, fact('f2')]), document([shared], lineage=1),
     expected([shared, fact('f2')], lineage=1))

case('retirements keep the published order then local additions',
     based(retirements=[retirement('r1', ['s1'])]),
     document(retirements=[retirement('r1', ['s1']), retirement('r3', ['s3'])]),
     document(retirements=[retirement('r1', ['s1']), retirement('r2', ['s2'])]),
     expected(retirements=[retirement('r1', ['s1']), retirement('r2', ['s2']), retirement('r3', ['s3'])]))

case('one retirement changed on both sides joins its sources',
     based(retirements=[retirement('r1', ['s1'])]),
     document(retirements=[retirement('r1', ['s1', 's2'])]),
     document(retirements=[retirement('r1', ['s1', 's3'])]),
     expected(retirements=[retirement('r1', ['s1', 's2', 's3'])]))

case('a correction without a known time reopens a resolved cutoff',
     based(retirements=[retirement('r1', ['s1'])]),
     document(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c1'], retiredThroughAt=1000,
                                      retiredThroughAtFromCorrection=True)]),
     document(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c2'])]),
     expected(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c1', 'c2'])]))

case('two resolved corrections keep the later cutoff',
     based(retirements=[retirement('r1', ['s1'])]),
     document(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c1'], retiredThroughAt=1000,
                                      retiredThroughAtFromCorrection=True)]),
     document(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c2'], retiredThroughAt=3000,
                                      retiredThroughAtFromCorrection=True)]),
     expected(retirements=[retirement('r1', ['s1'], retiredBySourceMessageIds=['c1', 'c2'], retiredThroughAt=3000,
                                      retiredThroughAtFromCorrection=True)]))

case('a retirement the publisher pruned stays pruned',
     based(retirements=[retirement('r1', ['s1']), retirement('r2', ['s2'])]),
     document(retirements=[retirement('r1', ['s1']), retirement('r2', ['s2'])]),
     document(retirements=[retirement('r2', ['s2'])]),
     expected(retirements=[retirement('r2', ['s2'])]))

case('confirmations from both sides are kept',
     based(confirmations=[{'fingerprint': '1:2:3', 'factId': 'f1'}]),
     document(confirmations=[{'fingerprint': '1:2:3', 'factId': 'f1'}, {'fingerprint': '4:5:6', 'factId': 'f2'}]),
     document(confirmations=[{'fingerprint': '1:2:3', 'factId': 'f1'}, {'fingerprint': '7:8:9', 'factId': 'f3'}]),
     expected(confirmations=[{'fingerprint': '1:2:3', 'factId': 'f1'}, {'fingerprint': '7:8:9', 'factId': 'f3'},
                             {'fingerprint': '4:5:6', 'factId': 'f2'}]))

case('cold lists keep the published order then local additions',
     based(cold=[listed('c1')]),
     document(cold=[listed('c1'), listed('c3', status='superseded', supersededById='f1')]),
     document(cold=[listed('c1'), listed('r2', kind='reflection')]),
     expected(cold=[listed('c1'), listed('r2', kind='reflection'), listed('c3', status='superseded', supersededById='f1')]))

case('a listed row leaves the entries whatever either side did to it',
     based([shared, fact('f2')]),
     document([fact('f1', 'likes green tea'), fact('f2')]),
     document([fact('f2')], cold=[listed('f1', status='superseded', supersededById='f2')]),
     expected([fact('f2')], cold=[listed('f1', status='superseded', supersededById='f2')]))

case('a listed reflection leaves the entries too',
     based([reflection('r1')]), document([reflection('r1', status='confirmed')]),
     document(cold=[listed('r1', kind='reflection')]),
     expected(cold=[listed('r1', kind='reflection')]))

case('without a base a listed row is not brought back as new',
     None, document([shared, fact('f2')]), document(cold=[listed('f1')]),
     expected([fact('f2')], cold=[listed('f1')]))

case('a cold row the organizer pruned stays pruned',
     based(cold=[listed('c1'), listed('c2')]),
     document(cold=[listed('c1', at=900), listed('c2')]),
     document(cold=[listed('c2')]),
     expected(cold=[listed('c2')]))

case('a cold row pruned here stays pruned while the remote did not change it',
     based(cold=[listed('c1'), listed('c2')]),
     document(cold=[listed('c2')]),
     document(cold=[listed('c1'), listed('c2')]),
     expected(cold=[listed('c2')]))

case('one row both lists changed takes the later status and the later time',
     based(cold=[listed('r1', kind='reflection', at=100), listed('c1', status='superseded', at=100, supersededById='f2')]),
     document(cold=[listed('r1', kind='reflection', at=700), listed('c1', status='superseded', at=600, supersededById='f3')]),
     document(cold=[listed('r1', kind='reflection', at=300), listed('c1', status='retracted', at=200)]),
     expected(cold=[listed('r1', kind='reflection', at=700), listed('c1', status='retracted', at=600)]))

case('a whole demoted row from an older writer is read as its cold listing',
     based([shared]),
     document([shared]),
     document([fact('f1', archived=True, archivedAt=800, status='superseded', supersededById='f9'),
               reflection('r1', archived=True, archivedAt=700)]),
     expected(cold=[listed('f1', status='superseded', at=800, supersededById='f9'),
                    listed('r1', kind='reflection', at=700)]))

legacy = {'kind': 'fact', 'subject': 'master', 'id': 'f1', 'text': 'likes tea', 'metadata': {'importance': 5}}
case('facts from an older document count as already judged',
     {'document': older([legacy]), 'nativeEntries': [legacy]},
     document([fact('f1', importance=5), fact('new', 'has a cat', signalJudged=False)]),
     older([legacy, {'kind': 'fact', 'subject': 'master', 'id': 'f3', 'text': 'has a dog', 'metadata': {}}]),
     expected([fact('f1', importance=5), fact('f3', 'has a dog'), fact('new', 'has a cat', signalJudged=False)]))

path = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'long_term_merge_cases.json'
with path.open('w', encoding='utf-8', newline='\n') as handle:
    json.dump({'cases': CASES}, handle, ensure_ascii=False, indent=1)
    handle.write('\n')
print(len(CASES), 'cases')
