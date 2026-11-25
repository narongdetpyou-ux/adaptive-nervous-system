"""Known V4 cases only, never V5 held-out data."""
import json
from pathlib import Path
from v4_scenarios import generate_scenarios
from v5_simulation import simulate_case

def main():
    out=Path('v5_diagnostic_known');out.mkdir(exist_ok=False)
    seeds={4015,4031,4040,4008,4046}
    cases=[s for s in generate_scenarios('validation') if s.group=='generated_switching_fault' and s.seed in seeds]
    assert len(cases)==5
    for case in cases:
        for p in ('static','capacity_balance','v4_hybrid','v5_full'):
            row,trace=simulate_case(p,case,regime='delayed_noisy',detailed=True)
            (out/f'{case.seed}_{p}.json').write_text(json.dumps({'row':row,'trace':trace},allow_nan=False))
if __name__=='__main__':main()
