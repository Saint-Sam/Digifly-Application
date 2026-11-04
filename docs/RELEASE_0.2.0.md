## 0.2.0

This release brings the current NEURON performance improvements to both downloads.

- Faster circuit preparation and contact mapping.
- Direct binary voltage and current recordings, avoiding large temporary CSV files.
- Shared model settings to reduce repeated data in saved runs.
- Optional solver threads, with a thread count and CPU availability display.
- Fixes for missing recording links and Data Library table crashes.

The sixteen-cell Mac test reached Results in 47 seconds, compared with 616 seconds
before these changes. Every recorded voltage value and sample time matched.

Builds now use short version numbers: 0.2.0, 0.2.1 and so on.

### Downloads

Choose macOS arm64 for Apple silicon running macOS 14 or later, or Windows x64.
The Windows download includes an offline NEURON installer. Mac builds are
ad-hoc signed; Apple notarization is pending. Windows package tests run on
Server 2022; Windows 11 testing is still pending.
