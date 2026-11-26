# Workstation in GitHub Codespaces

This configuration runs the native Qt app from DigiFly Workstation v0.2.3.
The startup command starts the desktop and noVNC on forwarded port 6080.
Keep port 6080 **Private**. The Codespaces HTTPS tunnel is the authentication
boundary for the session. The VNC listener is internal to the container.

Open the **DigiFly Workstation** port in a browser and append
`/vnc.html?autoconnect=1&resize=scale` to the forwarded host URL. A phone can
open that same HTTPS address while signed into the GitHub account that owns
the running codespace. Resume the codespace after an idle stop.

The container configures the Linux NEURON, Arbor and BMTK BioNet Python
environment. Male CNS morphology, connectivity and any saved recording must
be registered in the workspace separately. Bundled MANC resources are a
different data release. GitHub Pages can link to the private forwarded URL.
