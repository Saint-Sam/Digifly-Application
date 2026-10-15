TITLE Digifly independently authored PDCM event-driven point cell

NEURON {
    ARTIFICIAL_CELL DigiflyPDCMEvent
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
    in_refractory
}

INITIAL {
    last_event = t
    in_refractory = 0
}

NET_RECEIVE (weight (pA)) {
    if (flag == 1) {
        in_refractory = 0
        m = v_reset
        last_event = t
    } else if (in_refractory == 0) {
        LOCAL interval
        interval = t - last_event
        : This discrete event rule is specified independently of the authors'
        : executable; a zero-weight input still advances both state variables.
        i = i * (1 - interval / tau_syn) + weight
        m = m + interval * ((v_reset - m) / tau_m + (i + Iext) / capacitance)
        last_event = t
        if (m > v_threshold) {
            m = v_reset + 30
            in_refractory = 1
            net_send(tau_ref, 1)
            net_event(t)
        }
    }
}
