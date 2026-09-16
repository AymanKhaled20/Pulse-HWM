"""Raw HID driver layer (vendor-free phase).

Goal: drive RGB hardware with NO vendor software requirement — the probe
answers "is a Pulse-compatible RGB device physically present", not "is
Synapse/iCUE/LGS installed".

Conventions (matching the vendor drivers):
  * Every driver here subclasses RgbDriver with the FROZEN contract.
  * hid_module is injected like AulaDriver — tests pass a fake transport,
    production loads `import hid`.
  * VENDID tables live next to the using driver; probes against them must
    tolerate the product family sharing one VID.
  * Protocols marked FIRST-PASS were written from community/OpenRGB
    research WITHOUT live traffic validation — the "UNVERIFIED" flag in
    docs/RGB.md tracks them; owner hardware tests settle each one.
"""
