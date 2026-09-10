NEURON {
  THREADSAFE
  POINT_PROCESS HeteroRectGap
  NONSPECIFIC_CURRENT i
  RANGE i, g, gmax_open, gmax_closed, use_transfer, vgap_xfer, orientation, vhalf, vslope
  RANGE empirical_residual_frac, tau_open_ms, tau_close_ms, gate_state
  POINTER vgap_ptr
}

PARAMETER {
  gmax_open = 0 (nanosiemens)
  gmax_closed = 0 (nanosiemens)
  orientation = 1
  vhalf = 0 (millivolt)
  vslope = 5 (millivolt)
  empirical_residual_frac = 0.20
  tau_open_ms = 6 (ms)
  tau_close_ms = 2 (ms)
  use_transfer = 0
}

ASSIGNED {
  v (millivolt)
  i (nanoamp)
  vgap_ptr (millivolt)
  vgap_xfer (millivolt)
  g (nanosiemens)
  vpeer (millivolt)
  vj_oriented (millivolt)
  gate
  g_floor (nanosiemens)
}

STATE {
  gate_state
}

INITIAL {
  if (use_transfer > 0.5) {
    vpeer = vgap_xfer
  } else {
    vpeer = vgap_ptr
  }
  vj_oriented = orientation * (v - vpeer)
  gate_state = 1.0 / (1.0 + exp(-(vj_oriented - vhalf) / vslope))
}

BREAKPOINT {
  SOLVE gate_dynamics METHOD derivimplicit

  gate = gate_state
  g_floor = gmax_closed
  if (g_floor < gmax_open * empirical_residual_frac) {
    g_floor = gmax_open * empirical_residual_frac
  }
  g = g_floor + (gmax_open - g_floor) * gate
  i = (v - vpeer) * g * 0.001
}

DERIVATIVE gate_dynamics {
  LOCAL gate_inf, tau_gate

  if (use_transfer > 0.5) {
    vpeer = vgap_xfer
  } else {
    vpeer = vgap_ptr
  }
  vj_oriented = orientation * (v - vpeer)
  gate_inf = 1.0 / (1.0 + exp(-(vj_oriented - vhalf) / vslope))

  if (gate_inf > gate_state) {
    tau_gate = tau_open_ms
  } else {
    tau_gate = tau_close_ms
  }
  gate_state' = (gate_inf - gate_state) / tau_gate
}
