# sequences/ — the antibody sequences, in one place

- `trastuzumab_WT_reference.fasta` — wild-type trastuzumab (Herceptin), full heavy (449 aa)
  and light (214 aa) chain. The reference everything is compared against:
  - optimisation track keeps CDR-H3 >=70% identical to this
  - de novo track aims for <70% (novel)

More sequences (the generated designs) live on the VM under ~/her2-work and come into the
registry through the pipeline; the strongest are added here when selected.
