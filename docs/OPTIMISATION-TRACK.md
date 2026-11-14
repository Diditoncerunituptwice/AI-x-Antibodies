# Optimisation track — trastuzumab stability (from the bio teammate)

Status: **documented only. Not ingested into the registry.** Nobody has asked us to run it.

## Brief (as received, 2026-11-14)
Predict stabilising mutations in the Fab of trastuzumab (anti-HER2) while keeping antigen affinity
and the identity of key specificity regions (CDR-H3).

Background given: Fab Tm ~82 C; CH2 is the weaker domain (71-72 C). A double mutant
**T32Y (CDR-H1) + G103A (CDR-H3)** was already designed: Fab Tm ~87-90 C, CDR-H3 (Kabat 95-102)
identity to WT preserved, affinity predicted maintained or slightly better.

Goal: propose further mutations, alone or combined, with these filters:
- predicted ddG_fold > 0 (stabilising)
- predicted ddG_bind neutral to mildly positive
- CDR-H3 (Kabat 95-102) identity to WT: **70%** (comparison sign was lost in the paste: confirm "at least")
- fewer than 4 total mutations (sign lost in the paste: confirm "at most 4" vs "fewer than 4")

Deliverables: 10-20 proposed mutations with rationale; homology models of top 3-5;
mutant comparison table (ddG_fold, ddG_bind, CDR-H3 %ID, #mut, sites, mechanism, dTm);
1-2 paragraph recommendation.

## Known sequence (heavy chain variable domain, T32Y + G103A)
VH: EVQLVESGGGLVQPGGSLRLSCAASGFNIKDYYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKGRFTISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDAFYAMDYWGQGTLVTVSS
VL (unchanged): DIQMTQSPSSLSASVGDRVTITCRASQDVNTAVAWYQQKPGKAPKLLIYSASFLYSGVPSRFSGSRSGTDFTLTISSLQPEDFATYYCQQHYTTPPTFGQGTKVEIK
(Full chains were also sent: HC 449 aa, LC 214 aa.)

## What this means for our pipeline
- The 70% rule has a different direction here than in the de novo track:
  optimisation = keep CDR-H3 close to WT (>=70% identical); de novo = novel (<70%).
- Our `paratope_identity` filter currently marks anything >0.70 as "duplicate". That is correct
  for de novo and **wrong for optimisation**. It needs a per-track setting before the
  trastuzumab reference is loaded. Not built yet: waiting for the rule to be confirmed.
- Candidate format would be `fab` (VH + VL), see spec 0.1.3.

## Open questions
1. What is the "70%" measured against in the official hackathon rule (WT parent vs a database)?
2. Confirm the two lost comparison signs above.
3. Does the bio teammate want his designs in the registry, or only the written deliverable?
