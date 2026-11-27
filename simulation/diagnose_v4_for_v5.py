"""Known V4 cases only, never V5 held-out data."""
import json
from pathlib import Path
from v4_scenarios import generate_scenarios
from v5_simulation import simulate_case

KNOWN_SEEDS = (4008, 4015, 4031, 4040, 4046)


def known_cases():
    # In V4's namespace 'validation' is the published 4000..4063 population.
    cases = [case for case in generate_scenarios('validation')
             if case.group == 'generated_switching_fault' and case.seed in KNOWN_SEEDS]
    if sorted(case.seed for case in cases) != list(KNOWN_SEEDS):
        raise ValueError('Known V4 switching cases are incomplete')
    return sorted(cases, key=lambda case: case.seed)


def main():
    out=Path('v5_diagnostic_known');out.mkdir(exist_ok=False)
    cases=known_cases()
    for case in cases:
        for p in ('static','capacity_balance','v4_hybrid','v5_full'):
            row,trace=simulate_case(p,case,regime='delayed_noisy',detailed=True)
            (out/f'{case.seed}_{p}.json').write_text(json.dumps({'row':row,'trace':trace},allow_nan=False))
if __name__=='__main__':main()
