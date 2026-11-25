"""Select a queue ordering using only the development split."""
import json
from measure import RESULT,measure,report
CANDIDATES=['size_30_0.02','size_60_0.02','size_120_0.02','size_240_0.02','size_600_0.03','size_120_0.12']
if __name__=='__main__':
    for arm in ('fixed4','oldest_ready','current_v5',*CANDIDATES):
        measure('development',arm)
    reports=[report('development',arm) for arm in CANDIDATES]
    winner=min(reports,key=lambda d:d['rank'])['arm']
    (RESULT/'selection.json').write_text(json.dumps({'winner':winner,'candidates':reports},indent=2)+'\n')
    print(winner)
