# Workstation in GitHub Codespaces

This configuration runs the native Qt app from DigiFly Workstation v0.2.3.
The container starts the desktop and noVNC on forwarded port 6080. Startup
messages also appear in `~/.local/state/digifly-browser/session.log`.
Keep port 6080 **Private**. The Codespaces HTTPS tunnel is the authentication
boundary for the session. The VNC listener is internal to the container.

Open the **DigiFly Workstation** port in a browser to load the entire site.
Choose **DigiFly App** to open the embedded Workstation. A phone can
open that same HTTPS address while signed into the GitHub account that owns
the running codespace. Resume the codespace after an idle stop.

The container configures the Linux NEURON, Arbor and BMTK BioNet Python
environment. Male CNS morphology, connectivity and any saved recording must
be registered in the workspace separately. Bundled MANC resources are a
different data release. GitHub Pages can link to the private forwarded URL.
