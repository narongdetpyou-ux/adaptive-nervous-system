"""Observable evidence that moves cannot pay off (v6_acceptance_predeclared.json, mechanism `restraint`).

Three signals, each from data every policy already receives:
- switching: the unchanged V5 RegimeDetector sees rate changes at two or more
  stages within 12 minutes;
- reversal: the detector sees a stage's rate change and change back within the
  time a move needs to take effect and become revisable
  (transit + startup + dwell), the signature of a short pulse;
- bounce: after one of the controller's own moves arrives, the committed staff
  recorded by the environment ledger falls short of the intended allocation.
No failure window, sensor multiplier, family label or true rate is used.
"""
import numpy as np


class RestraintMonitor:
    def __init__(self, environment, dwell, memory):
        if memory < 0:
            raise ValueError('Restraint memory must be non-negative')
        self.environment = environment
        self.reach = environment.transit_delay + len(environment.startup_factors) + dwell
        self.memory = memory
        self.pending = []
        self.bounces = []
        self.reversals = []
        self._seen = set()
        self._last_event = {}

    def note_detector(self, events):
        """Read the detector's event list (source minute, stage) in source order."""
        for source, stage in sorted(events):
            if (source, stage) in self._seen:
                continue
            self._seen.add((source, stage))
            previous = self._last_event.get(stage)
            if previous is not None and source - previous <= self.reach:
                self.reversals.append(source)
            self._last_event[stage] = source

    def record_move(self, t, staff, delta):
        """Remember the allocation the controller intends once its own move has arrived."""
        intended = np.asarray(staff, dtype=float) + np.asarray(delta, dtype=float)
        self.pending.append((t + self.environment.transit_delay + 1, intended))

    def note_staff(self, t, staff):
        """Compare committed staff with each arrived move's intended allocation."""
        staff = np.asarray(staff, dtype=float)
        waiting = []
        for due, intended in self.pending:
            if t < due:
                waiting.append((due, intended))
            elif np.abs(staff - intended).sum() >= 0.5:
                self.bounces.append(t)
        self.pending = waiting

    def reasons(self, t, switching):
        lag = self.environment.sensor_lag
        found = []
        if switching:
            found.append('switching')
        if any(0 <= t - lag - source <= self.memory for source in self.reversals):
            found.append('reversal')
        if any(0 <= t - minute <= self.memory for minute in self.bounces):
            found.append('bounce')
        return found
