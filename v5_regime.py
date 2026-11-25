"""Observation-only change indicator and switching stress paths."""
import numpy as np

class RegimeDetector:
    def __init__(self):
        self.last = np.full(3, np.nan)
        self.events = []
        self.stage = None

    def observe(self, source, staff, flow, queue):
        if source < 0:
            return
        for i in range(3):
            if staff[i] > 0 and queue[i] > 10:
                rate = flow[i] / staff[i]
                if np.isfinite(self.last[i]) and abs(rate-self.last[i]) > .25*max(self.last[i], 1.):
                    self.events.append((source, i))
                self.last[i] = rate
        self.events = [(s,i) for s,i in self.events if s >= source-12]
        self.stage = int(np.argmax(queue)) if np.max(queue)>10 else None

    @property
    def switching(self):
        return len({i for _, i in self.events}) >= 2

def switching_paths(point, lower, upper, horizon, switching):
    if not switching:
        return []
    paths = []
    for a in range(3):
        for b in range(3):
            if a == b:
                continue
            path = np.tile(point, (horizon,1))
            split = max(1,horizon//2)
            path[:split,a] = lower[a]
            path[split:,a] = upper[a]
            path[split:,b] = lower[b]
            paths.append(path)
    return paths
