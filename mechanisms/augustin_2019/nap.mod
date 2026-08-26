COMMENT
Persistent sodium channel from Augustin, Zylbertal & Partridge (2019),
ModelDB 245415. Equations and parameters are unchanged from nap.mod; this
app-owned copy has normalized formatting and records the upstream byte hash in
the Digifly profile contract.
ENDCOMMENT

NEURON {
    SUFFIX nap
    USEION na READ ena WRITE ina
    RANGE m, gna, gbar, ina
    RANGE minf, mtau
    GLOBAL v1_2m, km
    GLOBAL vmin, vmax
}

PARAMETER {
    gbar = 0 (S/cm2)
    v1_2m = -48.77 (mV)
    km = -3.68 (mV)
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
    ina (mA/cm2)
    gna (S/cm2)
    ena (mV)
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
    gna = gbar*m
    ina = gna*(v-ena)
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
