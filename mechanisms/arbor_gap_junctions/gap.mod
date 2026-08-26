NEURON {
    JUNCTION_PROCESS gap
    NONSPECIFIC_CURRENT i
    RANGE g
}

UNITS {
    (mV) = (millivolt)
    (nA) = (nanoamp)
    (uS) = (microsiemens)
}

PARAMETER {
    g = 0 (uS)
}

INITIAL {}

BREAKPOINT {
    i = g*(v - v_peer)
}
