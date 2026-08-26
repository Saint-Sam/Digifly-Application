COMMENT
Potassium channel from Augustin, Zylbertal & Partridge (2019), ModelDB
245415. Equations and parameters are unchanged from k.mod; this app-owned copy
has normalized formatting and records the upstream byte hash in the Digifly
profile contract.
ENDCOMMENT

NEURON {
    SUFFIX k
    USEION k READ ek WRITE ik
    RANGE m, gk, gbar, ik
    RANGE minf, mtau
    GLOBAL v1_2m, km
    GLOBAL vmin, vmax
}

PARAMETER {
    gbar = 0 (S/cm2)
    v1_2m = -12.85 (mV)
    km = -19.91 (mV)
    v (mV)
    dt (ms)
    celsius (degC)
    vmin = -120 (mV)
    vmax = 1000 (mV)
}

UNITS {
    (mA) = (milliamp)
    (mV) = (millivolt)
}

ASSIGNED {
    ik (mA/cm2)
    gk (S/cm2)
    ek (mV)
    minf
    mtau (ms)
}

STATE { m }

INITIAL {
    trates(v)
    m = minf
}

BREAKPOINT {
    SOLVE states METHOD cnexp
    gk = gbar*m
    ik = gk*(v-ek)
}

DERIVATIVE states {
    trates(v)
    m' = (minf-m)/mtau
}

PROCEDURE trates(v (mV)) {
    TABLE minf, mtau
    DEPEND v1_2m, km
    FROM vmin TO vmax WITH 1600
    rates(v)
}

PROCEDURE rates(vm (mV)) {
    minf = 1/(1+exp((vm-v1_2m)/km))
    mtau = 1
}
