"""Compare deterministic evidence from two completed frozen v0.3 runs.

This compares existing artifacts and never runs, tunes or changes a policy.
Workflow timing, absolute runtime paths and event timestamps are intentionally
not byte-compared. A repeated run adds no independent statistical cases.
"""
import argparse
import hashlib
import json
from pathlib import Path

from freeze_v3 import verify_frozen
from v3_scenarios import REGIMES, POLICIES


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    if first == second:
        raise ValueError('Reproduction requires two different result directories')
    identity = verify_frozen()
    verifications = []
    for directory in (first, second):
        evidence = json.loads((directory/'verification.json').read_text())
        if evidence['status'] != 'SUCCEEDED' or evidence['split'] != 'validation':
            raise ValueError('Both validation workflows must have completed successfully')
        if evidence['freeze_verification'] != identity:
            raise ValueError('Run does not match the current frozen experiment')
        if sha(directory/'summary.json') != evidence['summary_sha256']:
            raise ValueError('Summary hash differs from workflow evidence')
        verifications.append(evidence)
    names = ['summary.json', 'episodes.csv', 'scenario_manifest.json',
             'representative_traces_index.json']
    for regime in REGIMES:
        for policy in POLICIES:
            names.extend([f'rows/{regime}__{policy}.json',
                          f'traces/{regime}__{policy}.json.gz'])
    comparisons = []
    for name in names:
        a, b = first/name, second/name
        same = a.read_bytes() == b.read_bytes()
        comparisons.append({'path':name, 'bytes':a.stat().st_size,
                            'original_sha256':sha(a), 'repeat_sha256':sha(b),
                            'byte_identical':same})
    if not all(item['byte_identical'] for item in comparisons):
        raise ValueError('Deterministic evidence differs between completed runs')
    return {
        'version':'0.3.0', 'status':'PASS',
        'protocol_sha256':identity['protocol_sha256'],
        'files_compared':len(comparisons), 'all_byte_identical':True,
        'comparisons':comparisons,
        'original_scope':verifications[0]['expected_scope'],
        'repeat_scope':verifications[1]['expected_scope'],
        'original_verification_sha256':sha(first/'verification.json'),
        'repeat_verification_sha256':sha(second/'verification.json'),
        'repeat_verification':verifications[1],
        'scope':'One complete fresh repetition of the same frozen validation. '
                'This is reproducibility evidence, not additional independent cases.',
        'exclusions':'Runtime event times, wall time, absolute paths and runtime databases '
                     'are not expected to be byte-identical.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first',type=Path,required=True)
    parser.add_argument('--second',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('Use a new report path; existing evidence is preserved')
    result = compare(args.first,args.second)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('status','files_compared','all_byte_identical','scope')}))
