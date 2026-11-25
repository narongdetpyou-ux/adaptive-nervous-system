"""Focused dispatcher checks. Run using the same pinned V5 source as the benchmark."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from queue_engine import Queue

SMALL='M31-J-256'
LARGE='M31-J-1280'


class TailPriorityTest(unittest.TestCase):
    def test_capped_older_job_can_get_service(self):
        jobs=[[0,[LARGE]*8],[90,[SMALL]]]
        for arm,expected in [('size_600_0.03',1),('tail_3_0.08',0)]:
            q=Queue(jobs,{SMALL:6,LARGE:6},arm)
            q.admit(90)
            self.assertEqual(q.pop()[0],expected)

    def test_uncapped_small_request_keeps_its_priority(self):
        q=Queue([[0,[LARGE]*8],[0,[SMALL]]],{SMALL:6,LARGE:6},'tail_3_0.08')
        q.admit(0)
        self.assertEqual(q.pop()[0],1)

    def test_cap_recomputed_for_remaining_tiles(self):
        q=Queue([[0,[LARGE]*4+[SMALL]]],{SMALL:6,LARGE:6},'tail_3_0.08')
        q.admit(0)
        self.assertEqual(q.priorities[0],(0,3.))
        q.start_tile(0,4)
        self.assertLess(q.priorities[0][1],3.)

    def test_bad_parameters(self):
        for arm in ('tail_0_0.1','tail_nan_0.1','tail_3_-1','tail_3_inf'):
            with self.subTest(arm=arm),self.assertRaises(ValueError):
                Queue([],{SMALL:6},arm)


if __name__=='__main__':
    unittest.main()
