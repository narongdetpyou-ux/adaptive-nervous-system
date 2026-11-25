import unittest
from experiment import FirstStartQueue,INC,NEW

SMALL='M31-J-256'
LARGE='M31-J-1280'
COUNTS={SMALL:4,LARGE:4}


def make(arm=NEW):
    return FirstStartQueue([[0,[LARGE]*4],[0,[SMALL]],[0,[LARGE]*4]],COUNTS,arm)


class DispatchTests(unittest.TestCase):
    def test_threshold_is_inclusive(self):
        for t,job in ((7.999,1),(8.,0)):
            queue=make()
            queue.advance(t)
            queue.admit(t)
            self.assertEqual(queue.pop(),(job,0,0,0))

    def test_reverts_immediately_and_handles_tied_requests(self):
        q=make()
        q.advance(8);q.admit(8)
        self.assertEqual([q.pop() for _ in range(3)],[(0,0,0,0),(2,0,0,0),(1,0,0,0)])
        self.assertEqual(q.changed_dispatches,[0,2])
        self.assertEqual(q.busy,[3,0,0])
        self.assertAlmostEqual(q.waiting[0],2.25)

    def test_already_started_and_small_jobs_receive_no_grant(self):
        q=FirstStartQueue([[0,[LARGE]*4],[30,[SMALL]]],COUNTS,NEW)
        q.admit(0)
        self.assertEqual(q.pop(),(0,0,0,0))
        q.advance(40);q.admit(40);q.pop()
        self.assertEqual(q.eligible_grants,[])

    def test_normal_choice_is_recorded_without_double_charge(self):
        q=FirstStartQueue([[0,[LARGE]*4]],COUNTS,NEW)
        q.advance(12);q.admit(12)
        self.assertEqual(q.pop(),(0,0,0,0))
        self.assertEqual((q.eligible_grants,q.changed_dispatches),([0],[]))
        self.assertEqual(q.pop(),(0,0,0,1))
        self.assertEqual(q.busy[0],2)
        self.assertEqual(q.waiting[0],.5)

    def test_overriding_a_background_dispatch_preserves_its_task(self):
        q=FirstStartQueue([[0,[SMALL]],[0,[LARGE]*4]],{SMALL:1,LARGE:4},NEW)
        q.admit(0)
        first=q.pop()
        q.advance(8);q.complete(first,8)
        self.assertEqual(q.pop(),(1,0,0,0))
        self.assertEqual(q.pop(),(0,0,1,0))
        self.assertEqual(q.busy,[1,1,0])
        self.assertEqual(q.waiting[1],0.)

    def test_normal_arm_never_changes_priority(self):
        q=make(INC);q.advance(9);q.admit(9)
        self.assertEqual(q.pop(),(1,0,0,0))
        self.assertEqual(q.eligible_grants,[])


if __name__=='__main__':
    unittest.main()
