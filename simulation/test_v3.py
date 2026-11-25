"""Tests for v0.3 physical delay timing, censoring and causal control contracts."""
import copy
from itertools import product
import unittest
from unittest.mock import patch

import numpy as np

from audit_v3 import (ReferenceTransitLedger, reference_service, tail_clearance,
                      audit_trace,audit_rows,audit_controller_replay)


def development_case(group):
    from v3_scenarios import generate_scenarios
    return next(case for case in generate_scenarios("development") if case.group==group and case.seed==1000)


def observe_fixed_allocation(policy, arrivals, rates):
    """Feed actual fluid observations at a fixed allocation for estimator tests.

    Used with warmup=100 to isolate inference, never as a benchmark policy.
    """
    queue=[0.0]*3
    history=[]
    notes=[]
    for t,arrival in enumerate(arrivals):
        observed=queue.copy();observed[0]+=float(arrival)
        delta,note=policy.decide(t,np.array(observed),history,np.full(3,69.),float(arrival))
        if np.any(delta):raise AssertionError("Fixed-observation fixture unexpectedly transferred")
        state=reference_service(queue,float(arrival),[69.]*3,rates[t])
        queue=state["queue"]
        policy.observe(t,np.full(3,69.),np.array(state["flow"]),np.array(queue))
        history=(history+[np.array(queue)])[-5:]
        notes.append(note)
    return queue,history,notes


class TransitReferenceTests(unittest.TestCase):
    @staticmethod
    def one_transfer(delay, horizon=10, move_minute=5):
        ledger = ReferenceTransitLedger(delay)
        records = []
        for t in range(horizon):
            records.append(ledger.step(t, [-30,30,0] if t == move_minute else [0,0,0]))
        return ledger, records

    def test_delay_zero_activates_in_same_service_interval(self):
        ledger, minutes = self.one_transfer(0)
        self.assertEqual(minutes[5]["active"], [39.0,99.0,69.0])
        self.assertEqual(minutes[5]["committed"], [39.0,99.0,69.0])
        self.assertEqual(minutes[5]["in_transit"], [0.0]*3)
        self.assertEqual(ledger.accounting()["resource_minutes_in_transit"], 0.0)

    def test_recipient_waits_exactly_delay_full_service_intervals(self):
        for delay in (1,3):
            with self.subTest(delay=delay):
                ledger, minutes = self.one_transfer(delay)
                for t in range(5,5+delay):
                    self.assertEqual(minutes[t]["active"], [39.0,69.0,69.0])
                    self.assertEqual(minutes[t]["committed"], [39.0,99.0,69.0])
                    self.assertEqual(minutes[t]["in_transit"], [0.0,30.0,0.0])
                self.assertEqual(minutes[5+delay]["active_before_decision"], [39.0,99.0,69.0])
                self.assertEqual(minutes[5+delay]["in_transit"], [0.0]*3)
                self.assertEqual(ledger.accounting()["resource_minutes_in_transit"], 30.0*delay)
                self.assertEqual(ledger.accounting()["unobserved_future_resource_minutes"], 0.0)

    def test_end_of_window_separates_actual_from_future_transfer_cost(self):
        ledger, _ = self.one_transfer(3, horizon=6)
        check = ledger.accounting()
        self.assertEqual(check["scheduled_resource_minutes"], 90.0)
        self.assertEqual(check["resource_minutes_in_transit"], 30.0)
        self.assertEqual(check["unobserved_future_resource_minutes"], 60.0)

    def test_hold_does_not_reset_timer_or_dwell(self):
        ledger = ReferenceTransitLedger(3)
        for t in range(14):
            delta = [-30,30,0] if t==5 else [30,-30,0] if t==13 else [0,0,0]
            minute = ledger.step(t, delta)
            if 5 <= t < 13:
                self.assertEqual(ledger.last_transfer, 5)
            if t==8:
                self.assertEqual(minute["active_before_decision"], [39.0,99.0,69.0])
        self.assertEqual(ledger.last_transfer,13)
        with self.assertRaisesRegex(ValueError,"warmup or dwell"):
            ledger.step(14,[-7,7,0])

    def test_busy_capacity_inference_uses_active_resources(self):
        _, minutes = self.one_transfer(3)
        state=minutes[5]
        service=reference_service([100,100,100],0.0,state["active"],[1,2,1])
        self.assertEqual(service["flow"],[39.0,138.0,69.0])
        self.assertEqual(service["queue"],[61.0,1.0,169.0])
        from capacity_policy import RateEstimator
        estimate=RateEstimator()
        estimate.update(0,np.array(state["active"]),np.array(service["flow"]),np.array(service["queue"]))
        np.testing.assert_array_equal(estimate.snapshot(),[1.0,2.0,1.0])
        self.assertNotEqual(service["flow"][1]/state["committed"][1],2.0)


class TailCensoringTests(unittest.TestCase):
    def test_clearance_delay_zero_and_first_tail_interval(self):
        self.assertEqual(tail_clearance([5.0,0.0,0.0],2)["clearance_tail_minutes"],0)
        self.assertEqual(tail_clearance([5.0,4.0,0.0],2)["clearance_tail_minutes"],1)

    def test_uncleared_tail_is_censored_not_a_fake_clearance_time(self):
        result=tail_clearance([5.0,4.0,3.0],2)
        self.assertTrue(result["clearance_censored"])
        self.assertIsNone(result["clearance_tail_minutes"])
        self.assertEqual(result["tail_length"],1)


class TailRiskMetricTests(unittest.TestCase):
    def test_cvar_uses_fractional_tail_mass(self):
        from v3_metrics import empirical_cvar
        # Seventeen cases have a 1.7-case 10% tail, not a rounded top-two mean.
        self.assertAlmostEqual(empirical_cvar([0.0]*16+[10.0]),10.0/1.7)
        self.assertAlmostEqual(empirical_cvar(list(range(17))),(16.0+0.7*15.0)/1.7)
        self.assertEqual(empirical_cvar([0.0]*17),0.0)
        self.assertEqual(empirical_cvar([0.0,3.0,6.0],tail_fraction=1.0),3.0)


class ProductionTransitTests(unittest.TestCase):
    def test_production_ledger_matches_independent_timing(self):
        from v3_simulation import TransitLedger
        for delay in (0,1,3):
            with self.subTest(delay=delay):
                production=TransitLedger(delay);reference=ReferenceTransitLedger(delay)
                loss=0.0
                for t in range(18):
                    delta=np.array([-30,30,0] if t==5 else [30,-30,0] if t==13 else [0,0,0],dtype=float)
                    production.start(t);production.apply(t,delta)
                    expected=reference.step(t,delta)
                    np.testing.assert_array_equal(production.active,expected["active"])
                    np.testing.assert_array_equal(production.committed,expected["committed"])
                    np.testing.assert_array_equal(production.pending,expected["in_transit"])
                    loss+=float(production.pending.sum())
                self.assertEqual(loss,60.0*delay)

    def test_delay_predictor_matches_scalar_service_and_does_not_charge_hold(self):
        from v3_policy import transition_prediction
        from capacity_policy import prediction_costs
        old=np.array([69.,69.,69.]);allocations=np.array([[69.,69.,69.],[39.,99.,69.]])
        rates=np.array([[[1.,2.,1.]]*8,[[1.4,.8,1.1]]*8])
        arrivals=np.array([[999999.,100.,200.,0.,50.,80.,100.,60.],[0.,150.,250.,0.,50.,70.,110.,80.]])
        queues=np.array([1000.,5000.,1000.])
        no_delay,_=transition_prediction(queues,old,allocations,rates,arrivals,0)
        np.testing.assert_allclose(no_delay,prediction_costs(queues,allocations,rates,arrivals))
        for delay in (0,1,3):
            predicted,terminal=transition_prediction(queues,old,allocations,rates,arrivals,delay)
            np.testing.assert_allclose(predicted[:,0],no_delay[:,0])
            self.assertTrue(np.all(predicted[:,1]>=no_delay[:,1]-1e-9))
            for model in range(2):
                for choice in range(2):
                    ledger=ReferenceTransitLedger(delay)
                    for t in range(5):ledger.step(t,[0,0,0])
                    q=queues.tolist();backlogs=[]
                    for h in range(8):
                        delta=allocations[choice]-old if h==0 else [0,0,0]
                        state=ledger.step(h+5,delta)
                        service=reference_service(q,0.0 if h==0 else arrivals[model,h],state["active"],rates[model,h])
                        q=service["queue"];backlogs.append(sum(q))
                    self.assertAlmostEqual(predicted[model,choice],sum(backlogs)/8+.25*sum(q))
                    self.assertAlmostEqual(terminal[model,choice],sum(q))

    def test_zero_delay_reproduces_frozen_v2_original_horizon(self):
        from capacity_simulation import simulate_case as old_simulate
        from v3_simulation import simulate_case as new_simulate
        for group in ("shifting_bottleneck","generated_switching_fault"):
            case=development_case(group)
            for old_policy,new_policy,seeds in (("capacity_aware","capacity_v2",(None,)),("abc_capacity","abc_capacity",(11,29,47))):
                for seed in seeds:
                    with self.subTest(group=group,policy=new_policy,seed=seed):
                        old_row,old_trace=old_simulate(old_policy,case,seed,True)
                        row,trace=new_simulate(new_policy,case,seed,"instant",True)
                        for field in ("mean_backlog","peak_backlog","end_backlog","arrivals","completed","moves"):
                            self.assertEqual(row[field],old_row[field])
                        for old,new in zip(old_trace,trace[:case.horizon]):
                            for field in ("t","arrival","observed","productivity","delta","flow","queue","branch","completed_cumulative","decision_evidence"):
                                self.assertEqual(old[field],new[field])
                            self.assertEqual(old["staff"],new["active_staff"])
                            self.assertEqual(old["staff"],new["committed_staff"])
                        self.assertEqual(row["resource_minutes_lost"],0.0)

    def test_trace_row_and_observation_replay_with_delay_and_followup(self):
        from v3_simulation import simulate_case,make_controller
        from v3_scenarios import REGIME_DELAYS
        case=development_case("generated_sustained_processing_fault")
        rows=[]
        policies=("static","capacity_v2","v3_full")
        for regime,delay in REGIME_DELAYS.items():
            for policy in policies:
                row,trace=simulate_case(policy,case,None,regime,True)
                rep={"summary":row,"minutes":trace}
                self.assertEqual(audit_trace(rep,case,regime)["minutes_replayed"],case.horizon+32)
                ctrl=make_controller(policy,case.seed,case.group,None,len(trace),delay)
                self.assertEqual(audit_controller_replay(rep,ctrl)["decisions_replayed"],len(trace))
                rows.append(row)
        checked=audit_rows(rows,[case],{policy:(None,) for policy in policies})
        self.assertEqual(checked["episodes_checked"],9)
        self.assertEqual(checked["original_minutes"],9*96)
        self.assertEqual(checked["followup_minutes"],9*32)

    def test_independent_auditor_rejects_transit_and_tail_corruption(self):
        from v3_simulation import simulate_case
        case=development_case("generated_sustained_processing_fault")
        row,trace=simulate_case("capacity_v2",case,None,"transit_3",True)
        rep={"summary":row,"minutes":trace}
        move_index=next(i for i,m in enumerate(trace) if any(m["delta"]))
        for field in ("active_staff","pending_staff","committed_staff","ready_at","resource_minutes_lost","flow","queue"):
            with self.subTest(field=field):
                bad=copy.deepcopy(rep)
                if isinstance(bad["minutes"][move_index][field],list):bad["minutes"][move_index][field][0]+=1.0
                else:bad["minutes"][move_index][field]+=1
                with self.assertRaises(ValueError):audit_trace(bad,case,"transit_3")
        bad=copy.deepcopy(row);bad["tail_completed"]+=1.0
        with self.assertRaisesRegex(ValueError,"Completion totals"):
            audit_rows([bad],[case],{"capacity_v2":(None,)},regimes=("transit_3",))
        with self.assertRaisesRegex(ValueError,"Missing, duplicated"):
            audit_rows([row,row],[case],{"capacity_v2":(None,)},regimes=("transit_3",))


class V3ObservationTests(unittest.TestCase):
    @staticmethod
    def before_second_busy_decision(mode="full",delay=3):
        from v3_policy import V3Policy
        policy=V3Policy(mode=mode,transit_delay=delay)
        queue=[0.0]*3;history=[];notes=[]
        for t in range(7):
            arrival=4000.0 if t<5 else 10000.0
            observed=queue.copy();observed[0]+=arrival
            delta,note=policy.decide(t,np.array(observed),history,np.full(3,69.),arrival)
            if np.any(delta):raise AssertionError("Transferred before two busy observations")
            rate=[200.0,50.0 if t>=5 else 200.0,200.0]
            physical=reference_service(queue,arrival,[69.]*3,rate)
            queue=physical["queue"]
            policy.observe(t,np.full(3,69.),np.array(physical["flow"]),np.array(queue))
            history=(history+[np.array(queue)])[-5:]
            notes.append(note)
        return policy,queue,history,notes

    def test_two_busy_observations_are_required_for_recipient_eligibility(self):
        policy,queue,history,notes=self.before_second_busy_decision()
        self.assertEqual(notes[6]["recent_busy_counts"],[0,1,0])
        self.assertEqual(notes[6]["branch"],"insufficient_capacity_evidence")
        queue[0]+=10000.0
        delta,note=policy.decide(7,np.array(queue),history,np.full(3,69.),10000.0)
        self.assertEqual(note["recent_busy_counts"],[0,2,0])
        self.assertGreater(note["candidate_count"],0)
        if any(delta):self.assertEqual(int(np.argmax(delta)),1)

    def test_full_forecast_has_independent_stage_corners_and_known_delay(self):
        import v3_policy
        original=v3_policy.transition_prediction
        for mode,count,modeled_delay in (("full",33,3),("signal_only",9,0)):
            with self.subTest(mode=mode):
                policy,queue,history,_=self.before_second_busy_decision(mode)
                queue[0]+=10000.0
                recorded={}
                def capture(queues,old,allocations,rates,arrivals,delay,terminal_weight):
                    recorded.update(rates=rates.copy(),delay=delay)
                    return original(queues,old,allocations,rates,arrivals,delay,terminal_weight)
                with patch("v3_policy.transition_prediction",side_effect=capture):
                    _,note=policy.decide(7,np.array(queue),history,np.full(3,69.),10000.0)
                self.assertEqual(note["forecast_models"],count)
                self.assertEqual(recorded["delay"],modeled_delay)
                if mode=="full":
                    low,high,_=policy.bounds(7)
                    expected={tuple(high[i] if bit[i] else low[i] for i in range(3)) for bit in product((0,1),repeat=3)}
                    actual={tuple(rate[0]) for rate in recorded["rates"][9:]}
                    self.assertEqual(actual,expected)
                    self.assertEqual(len(actual),8)

    def test_full_gate_enforces_declared_stress_allowance_and_terminal_guard(self):
        # Synthetic forecast outputs isolate acceptance boundaries; these are
        # unit-test fixtures and are never included in experiment results.
        for stress_fraction,terminal_increase,should_move in ((.049,0.,True),(.051,0.,False),(.0,1.,False)):
            with self.subTest(stress=stress_fraction,terminal=terminal_increase):
                policy,queue,history,_=self.before_second_busy_decision("full",3)
                queue[0]+=10000.
                def forecast_fixture(queues,old,allocations,rates,arrivals,delay,terminal_weight):
                    cost=np.full((len(rates),len(allocations)),1000.)
                    cost[:9,1:]=900.
                    cost[9:,1:]=1000.*(1+stress_fraction)
                    terminal=np.zeros_like(cost);terminal[:9,1:]=terminal_increase
                    return cost,terminal
                with patch("v3_policy.transition_prediction",side_effect=forecast_fixture):
                    delta,note=policy.decide(7,np.array(queue),history,np.full(3,69.),10000.)
                self.assertEqual(bool(np.any(delta)),should_move)
                self.assertEqual(note["stress_regression_fraction"],.05)
                if should_move:
                    self.assertLess(note["predicted_worst_gain"],0.)
                    self.assertEqual(note["branch"],"selected")
                else:self.assertEqual(note["branch"],"model_rejected")

    def test_five_busy_samples_can_override_overlap_but_not_an_empty_latest_queue(self):
        from v3_policy import V3Policy,V3Config
        persistent=V3Policy(config=V3Config(warmup=5))
        q,history,_=observe_fixed_allocation(persistent,np.full(5,30000.),np.full((5,3),200.))
        q[0]+=30000.
        _,note=persistent.decide(5,np.array(q),history,np.full(3,69.),30000.)
        self.assertEqual(note["recent_busy_counts"][0],5)
        self.assertGreater(note["candidate_count"],0)
        self.assertGreater(note["rate_upper"][0]*69.,note["rate_lower"][1]*69.)
        recovered=V3Policy(config=V3Config(warmup=7))
        arrivals=np.full(7,4000.);arrivals[4:6]=10000.
        rates=np.full((7,3),200.);rates[4:6,1]=50.;rates[6,1]=1000.
        q,history,_=observe_fixed_allocation(recovered,arrivals,rates)
        self.assertEqual(history[-1][1],0.)
        q[0]+=4000.
        delta,note=recovered.decide(7,np.array(q),history,np.full(3,69.),4000.)
        self.assertEqual(note["recent_busy_counts"][1],2)
        self.assertFalse(np.any(delta))
        self.assertEqual(note["branch"],"insufficient_capacity_evidence")

    def test_stale_low_rate_anchor_is_not_erased_by_prior_reversion(self):
        from v3_policy import V3Policy,V3Config
        policy=V3Policy(config=V3Config(warmup=100))
        arrivals=np.full(20,1000.);arrivals[0]=15000.
        rates=np.full((20,3),200.);rates[:6,1]=50.
        queue,history,_=observe_fixed_allocation(policy,arrivals,rates)
        lower,upper,counts=policy.bounds(20)
        self.assertEqual(counts[1],0)
        self.assertGreater(policy.estimator.snapshot()[1],150.)
        self.assertAlmostEqual(lower[1],42.5)
        # Actual high completed flow, even while censored, can supply a higher
        # fresh lower bound. It is not a synthetic recovery announcement.
        observed=queue.copy();observed[0]+=10000.
        policy.decide(20,np.array(observed),history,np.full(3,69.),10000.)
        service=reference_service(queue,10000.,[69.]*3,[200.]*3)
        policy.observe(20,np.full(3,69.),np.array(service["flow"]),np.array(service["queue"]))
        self.assertGreater(policy.bounds(21)[0][1],42.5)
        self.assertEqual(policy.last_busy_rate[1],50.)

    def test_hidden_current_and_future_changes_do_not_change_prior_decisions(self):
        from capacity_scenarios import Scenario
        from v3_simulation import simulate_case
        horizon=60
        arrivals=np.full(horizon,10000.);rates=np.full((horizon,3),200.)
        changed_arrivals=arrivals.copy();changed_arrivals[13:]=18000.
        changed_rates=rates.copy();changed_rates[12:,1]=45.
        a=Scenario("surge_only",1000,arrivals,rates,"legacy_flash")
        b=Scenario("surge_only",1000,changed_arrivals,changed_rates,"legacy_flash")
        for policy in ("v3_full","v3_signal_only","capacity_balance"):
            with self.subTest(policy=policy):
                _,one=simulate_case(policy,a,None,"transit_3",True)
                _,two=simulate_case(policy,b,None,"transit_3",True)
                for left,right in zip(one[:13],two[:13]):
                    self.assertEqual(left["delta"],right["delta"])
                    self.assertEqual(left["decision_evidence"],right["decision_evidence"])


if __name__=="__main__":
    unittest.main()
