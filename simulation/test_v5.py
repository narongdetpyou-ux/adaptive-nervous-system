import unittest
import numpy as np
from v4_environment import REGIME_CONFIGS
from v5_observer import ActionLedger
from v5_regime import RegimeDetector,switching_paths
from v5_forecast import loss_candidates
from v5_policy import V5Policy,VARIANTS

class V5Tests(unittest.TestCase):
    def test_loss_gate(self):
        allowed,_,_=loss_candidates(np.array([[5.,2.,7.],[4.,3.,8.]]),[0,1],1e-6)
        self.assertEqual(allowed.tolist(),[False,True,False])
    def test_loss_conflict(self):
        allowed,_,_=loss_candidates(np.array([[5.,0.,3.],[5.,8.,4.]]),[0,1],1e-6)
        self.assertEqual(allowed.tolist(),[False,False,True])
    def test_no_lag_reconstruction(self):
        ledger=ActionLedger(REGIME_CONFIGS['legacy_instant'])
        np.testing.assert_array_equal(ledger.reconstruct(8,[100,200,300],1000,np.full(3,200)),[100,200,300])
    def test_detector(self):
        d=RegimeDetector()
        d.observe(0,np.full(3,10),np.full(3,2000),np.full(3,100))
        d.observe(1,np.full(3,10),np.array([1000,1000,2000]),np.full(3,100))
        self.assertTrue(d.switching)
        self.assertEqual(len(switching_paths(np.full(3,200),np.full(3,100),np.full(3,300),16,True)),6)
    def test_warmup_all_variants(self):
        for variant in VARIANTS:
            p=V5Policy(REGIME_CONFIGS['delayed_noisy'],variant)
            a,_=p.decide(0,np.zeros(3),[],np.full(3,69),0.)
            np.testing.assert_array_equal(a,np.zeros(3))
            p.observe(0,np.full(3,69),np.zeros(3),np.zeros(3))
if __name__=='__main__':unittest.main()
