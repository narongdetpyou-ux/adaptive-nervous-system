import unittest
import json
import numpy as np
from simulation import ROOT, H, BASE, load_trace, scenarios, simulate, sha, verify
from lab_agents import validate_evaluation

class LabTests(unittest.TestCase):
    def setUp(self):
        self.trace=load_trace(ROOT/'data/load_test_summary_per_minute.csv')
    def test_source_integrity(self):
        for line in (ROOT/'source_checksums.sha256').read_text().splitlines():
            expected,path=line.split(maxsplit=1)
            self.assertEqual(sha(ROOT/path),expected,path)
        self.assertEqual(len(verify()),3)
    def test_scenario_reproducibility(self):
        a=list(scenarios(self.trace));b=list(scenarios(self.trace));self.assertEqual(len(a),40)
        for x,y in zip(a,b):
            self.assertEqual(x[:2],y[:2]);np.testing.assert_array_equal(x[2],y[2]);np.testing.assert_array_equal(x[3],y[3])
    def test_guard_blocks_false_signal(self):
        arr=np.full(H,1000.);prod=np.full((H,3),200.)
        row,detail=simulate('abc','surge_only',100,11,arr,prod,True,persistent_guard=True)
        self.assertEqual(row['moves'],0);self.assertEqual(row['mean_backlog'],0.)
        self.assertTrue(all(m['branch']=='gate_hold' for m in detail))
    def test_zero_traffic(self):
        for p in ('static','maxweight','abc'):
            row,_=simulate(p,'surge_only',100,11,np.zeros(H),np.full((H,3),200.))
            self.assertEqual(row['completed'],0);self.assertEqual(row['moves'],0)
    def test_static_underload(self):
        row,detail=simulate('static','surge_only',100,None,np.full(H,1000.),np.full((H,3),200.),True)
        self.assertEqual(row['completed'],H*1000.);self.assertEqual(row['mean_backlog'],0.)
        self.assertTrue(all(m['staff']==BASE.tolist() for m in detail))
    def test_bad_trace(self):
        with self.assertRaises(ValueError):list(scenarios([1]*29))
    def test_fresh_and_repeat_results(self):
        r=ROOT/'results';s=json.loads((r/'summary.json').read_text());v=json.loads((r/'verification.json').read_text())
        self.assertEqual((s['scenarios'],s['episodes'],s['minutes']),(40,200,12000))
        self.assertEqual(v['status'],'SUCCEEDED');self.assertEqual(v['steps']['abc'],2)
        self.assertFalse(v['duplicate_submission_created']);self.assertTrue(v['restart_replay_preserved_report'])
        self.assertEqual(v['negative_control']['steps']['blocked'],'BLOCKED')
    def test_corrupt_summary_rejected(self):
        from simulation import evaluate
        out=evaluate('static',self.trace);out['rows'][0]['completed']+=1
        with self.assertRaisesRegex(ValueError,'Lost work'):
            validate_evaluation({'input':{'trace':self.trace},'params':{'policy':'static'}},out)
if __name__=='__main__': unittest.main()
