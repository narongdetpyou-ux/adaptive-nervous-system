"""Development-only smoke; not a performance or promotion gate."""
from v4_scenarios import generate_scenarios
from v5_simulation import simulate_case
from v5_policy import VARIANTS
from v4_metrics import _row_metric_values

def main():
    cases=generate_scenarios('development')
    for group in ('generated_underload','generated_switching_fault'):
        case=next(s for s in cases if s.group==group)
        for regime in ('legacy_instant','delayed_noisy'):
            for policy in ('static','capacity_balance','v4_hybrid',*VARIANTS):
                row,_=simulate_case(policy,case,regime=regime)
                _row_metric_values(row)
                print(group,regime,policy,row['mean_deficit'],row['total_dropped'],flush=True)
if __name__=='__main__':main()
