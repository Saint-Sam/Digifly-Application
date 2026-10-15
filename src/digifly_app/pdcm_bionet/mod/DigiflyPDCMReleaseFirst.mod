TITLE Isolated inclusive-refractory-release sensitivity control

NEURON {
    ARTIFICIAL_CELL DigiflyPDCMReleaseFirst
    RANGE m, i, Iext
    RANGE tau_m, tau_syn, tau_ref, v_reset, v_threshold, capacitance
}
UNITS {
    (mV) = (millivolt)
    (pA) = (picoamp)
    (pF) = (picofarad)
}
PARAMETER {
    tau_m = 10 (ms)
    tau_syn = 0.5 (ms)
    tau_ref = 2 (ms)
    v_reset = -65 (mV)
    v_threshold = -50 (mV)
    capacitance = 250 (pF)
    Iext = 0 (pA)
}
ASSIGNED {
    m (mV)
    i (pA)
    last_event (ms)
    refractory_until (ms)
    in_refractory
}
INITIAL {
    last_event = t
    refractory_until = -1
    in_refractory = 0
}
NET_RECEIVE (weight (pA)) {
    LOCAL interval
    : Enforce inclusive expiry on both paths; stale callbacks do nothing.
    if (in_refractory == 1 && t >= refractory_until) {
        in_refractory = 0
        m = v_reset
        last_event = refractory_until
    }
    if (flag == 0 && in_refractory == 0) {
        interval = t - last_event
        i = i * (1 - interval / tau_syn) + weight
        m = m + interval * ((v_reset - m) / tau_m + (i + Iext) / capacitance)
        last_event = t
        if (m > v_threshold) {
            m = v_reset + 30
            in_refractory = 1
            refractory_until = t + tau_ref
            net_send(tau_ref, 1)
            net_event(t)
        }
    }
}
