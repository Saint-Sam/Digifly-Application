NEURON {
    JUNCTION_PROCESS hetero_rect_gap
    NONSPECIFIC_CURRENT i
    RANGE g, gmax_open, gmax_closed, orientation, vhalf, vslope
    RANGE empirical_residual_frac, tau_open_ms, tau_close_ms
}

UNITS {
    (mV) = (millivolt)
    (ms) = (millisecond)
    (nA) = (nanoamp)
    (uS) = (microsiemens)
}

PARAMETER {
    gmax_open = 0 (uS)
    gmax_closed = 0 (uS)
    orientation = 1
    vhalf = 0 (mV)
    vslope = 5 (mV)
    empirical_residual_frac = 0.20
    tau_open_ms = 6 (ms)
    tau_close_ms = 2 (ms)
}

ASSIGNED {
    g (uS)
    g_floor (uS)
    gate_inf
    tau_gate (ms)
    vj_oriented (mV)
}

STATE {
    gate_state
}

INITIAL {
    vj_oriented = orientation*(v - v_peer)
    gate_state = 1/(1 + exp(-(vj_oriented - vhalf)/vslope))
}

BREAKPOINT {
    SOLVE gate_dynamics METHOD cnexp

    g_floor = gmax_closed
    if (g_floor < gmax_open*empirical_residual_frac) {
        g_floor = gmax_open*empirical_residual_frac
    }
    g = g_floor + (gmax_open - g_floor)*gate_state
    i = g*(v - v_peer)
}

DERIVATIVE gate_dynamics {
    vj_oriented = orientation*(v - v_peer)
    gate_inf = 1/(1 + exp(-(vj_oriented - vhalf)/vslope))

    if (gate_inf > gate_state) {
        tau_gate = tau_open_ms
    } else {
        tau_gate = tau_close_ms
    }
    gate_state' = (gate_inf - gate_state)/tau_gate
}
