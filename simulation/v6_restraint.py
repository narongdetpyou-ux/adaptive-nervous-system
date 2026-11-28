"""Observable evidence that moves cannot pay off (v6_acceptance_predeclared.json, mechanism `restraint`).

Signals, each from data every policy already receives (the unchanged V5
RegimeDetector's rate-change events and the environment's committed-staff
ledger):
- fast switching: the last two intervals between rate-change events at
  different stages are both within the time a move needs to take effect and
  become revisable (transit + startup + dwell). Slow switching, where moving
  still pays, does not qualify.
- reversal: a stage's rate changes and changes back within that time, the
  signature of a short pulse.
- bounce: after one of the controller's own moves arrives, the committed staff
  falls short of the intended allocation.
- switching (the detector's own 12-minute two-stage flag) is kept only for the
  original hurdle mode (amendment 1 of v6_acceptance_predeclared.json).
No failure window, sensor multiplier, family label or true rate is used.
"""
import numpy as np


class RestraintMonitor:
    def __init__(self, environment, dwell, memory, *, detector_switching=False, fast_switching=True):
        if memory < 0:
            raise ValueError('Restraint memory must be non-negative')
        self.environment = environment
        self.reach = environment.transit_delay + len(environment.startup_factors) + dwell
        self.memory = memory
        self.detector_switching = detector_switching
        self.use_fast_switching = fast_switching
        self.pending = []
        self.bounces = []
        self.reversals = []
        self.fast_switches = []
        self._seen = set()
        self._last_event = {}
        self._last_switch_stage = None
        self._switch_times = []

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
            if self._last_switch_stage is not None and stage != self._last_switch_stage:
                self._switch_times.append(source)
                times = self._switch_times[-3:]
                if len(times) == 3 and times[2] - times[1] <= self.reach and times[1] - times[0] <= self.reach:
                    self.fast_switches.append(source)
            self._last_switch_stage = stage

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
        if self.detector_switching and switching:
            found.append('switching')
        if self.use_fast_switching and any(0 <= t - lag - source <= self.memory for source in self.fast_switches):
            found.append('fast_switching')
        if any(0 <= t - lag - source <= self.memory for source in self.reversals):
            found.append('reversal')
        if any(0 <= t - minute <= self.memory for minute in self.bounces):
            found.append('bounce')
        return found
