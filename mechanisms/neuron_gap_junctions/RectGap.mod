NEURON {
  THREADSAFE
  POINT_PROCESS RectGap
  NONSPECIFIC_CURRENT i
  RANGE i, g, gmax, use_transfer, vgap_xfer
  POINTER vgap_ptr
}

PARAMETER {
  gmax = 0 (nanosiemens)
  use_transfer = 0
}

ASSIGNED {
  v (millivolt)
  i (nanoamp)
  vgap_ptr (millivolt)
  vgap_xfer (millivolt)
  g (nanosiemens)
  vpeer (millivolt)
}

BREAKPOINT {
  if (use_transfer > 0.5) {
    vpeer = vgap_xfer
  } else {
    vpeer = vgap_ptr
  }
  if (vpeer > v) {
    g = gmax
  } else {
    g = 0
  }
  i = (v - vpeer) * g * 0.001
}
