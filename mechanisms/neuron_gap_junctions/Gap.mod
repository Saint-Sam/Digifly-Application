NEURON {
  THREADSAFE
  POINT_PROCESS Gap
  NONSPECIFIC_CURRENT i
  RANGE i, g, use_transfer, vgap_xfer
  POINTER vgap_ptr
}
PARAMETER {
  g = 0 (nanosiemens)
  use_transfer = 0
}
ASSIGNED {
  v (millivolt)
  i (nanoamp)
  vgap_ptr (millivolt)
  vgap_xfer (millivolt)
}
BREAKPOINT {
  if (use_transfer > 0.5) {
    i = (v - vgap_xfer) * g * 0.001
  } else {
    i = (v - vgap_ptr) * g * 0.001
  }
}
