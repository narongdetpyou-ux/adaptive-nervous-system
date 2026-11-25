# Research grounding for v0.4

Sources checked on 25 November 2026. These sources motivate modeling choices;
none proves stability, optimality, safety or tail-risk improvement for ANS.
All ANS numerical results come from the separately retained experiments.

## Switching overhead and adaptive decisions

- Ping-Chun Hsieh, I-Hong Hou and Xi Liu, *Delay-Optimal Scheduling for Queueing Systems with Switching Overhead*, [author paper on arXiv](https://arxiv.org/abs/1701.03831). Their scheduling model explicitly loses service capacity while switching. Their results concern a different queueing model. ANS uses the general motivation to model unavailable resources during a transfer instead of treating transfers as free.
- Chang-Heng Wang and Tara Javidi, *Adaptive Policies for Scheduling with Reconfiguration Delay*, [author paper on arXiv](https://arxiv.org/abs/1511.03417). The paper studies adaptive scheduling in an optical-network setting with nonzero reconfiguration delay. ANS uses the design idea of balancing responsiveness against repeated reconfiguration; its nonlinear pipeline, observation errors and resource-allocation catalogue are different.
- Johannes Koehler and coauthors, *Linear robust adaptive model predictive control: Computational complexity and conservatism*, [author paper on arXiv](https://arxiv.org/abs/1909.01813). The paper combines online identification with robust predictive control for linear systems under specific assumptions. It illustrates separating performance estimates from uncertainty handling. ANS has neither its tube construction nor its proof conditions; engineering stress scenarios are not calibrated confidence regions.

## Operational constraints

[Kubernetes Horizontal Pod Autoscaling documentation](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/) describes periodic control, readiness/startup treatment, missing or old metrics, scaling-rate limits, and stabilization behavior. These establish that an operational autoscaler needs to handle more than an instantaneous perfect utilization number. ANS is a fixed-budget three-stage allocation experiment, not an implementation or benchmark of Kubernetes HPA.

The values used in ANS -- 207 abstract resource units, buffers of 60,000/40,000/40,000 work units, three-minute transfers, startup fractions 50%/75%, two-minute sensor lag, and log-space noise sigma 0.05 -- are **prespecified synthetic stress assumptions**. They were not measured from a production system and are not recommended deployment settings or copied Kubernetes defaults.

## Diagnostic grounding in the user's project

`V4_DIAGNOSIS_NOTES.md` and `v4_results/diagnosis/diagnosis.json` reproduce known
v0.3 cases. They document an escalating backlog-relative move hurdle, stale
capacity anchors, delayed recovery, and situations in which unrestricted
balance also fails. Those concrete cases motivated a finite stress penalty,
a flow-scaled hurdle, longer forecast, limited move sizes, and model-neutral
idle recovery. They are development evidence; the reserved validation split
is evaluated only after the final configuration and source freeze.
