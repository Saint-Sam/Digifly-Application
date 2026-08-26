NEURON {
    JUNCTION_PROCESS rect_gap
    NONSPECIFIC_CURRENT i
    RANGE g, gmax
}

UNITS {
    (mV) = (millivolt)
    (nA) = (nanoamp)
    (uS) = (microsiemens)
}

PARAMETER {
    gmax = 0 (uS)
}

ASSIGNED {
    g (uS)
}

INITIAL {
    g = 0
}

BREAKPOINT {
    if (v_peer > v) {
        g = gmax
    } else {
        g = 0
    }
    i = g*(v - v_peer)
}
