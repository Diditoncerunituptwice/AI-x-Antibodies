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
- CDR-H3 (Kabat 95-102) identity to WT: **at least 70%** (confirmed, see Provenance below)
- fewer than 4 total mutations (sign still unconfirmed: "at most 4" vs "fewer than 4")

Deliverables: 10-20 proposed mutations with rationale; homology models of top 3-5;
mutant comparison table (ddG_fold, ddG_bind, CDR-H3 %ID, #mut, sites, mechanism, dTm);
1-2 paragraph recommendation.

## Known sequence (heavy chain variable domain, T32Y + G103A)
VH: EVQLVESGGGLVQPGGSLRLSCAASGFNIKDYYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKGRFTISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDAFYAMDYWGQGTLVTVSS
VL (unchanged): DIQMTQSPSSLSASVGDRVTITCRASQDVNTAVAWYQQKPGKAPKLLIYSASFLYSGVPSRFSGSRSGTDFTLTISSLQPEDFATYYCQQHYTTPPTFGQGTKVEIK
(Full chains were also sent: HC 449 aa, LC 214 aa.)

## Provenance — how the mutant was designed (bio teammate's working session, ~2026-11-14)
Structure-based, worked from the 1N8Z crystal structure (trastuzumab Fab bound to HER2).
All numbers below are **PREDICTED from one crystal structure — no MD, no Rosetta, no experiment.**
In our ontology these are predicted evidence, never experimental. The session itself flagged
that several side-chain models hit technical trouble and some ddG values are soft.

- G103A — fixes the Asp102-Gly103 isomerisation hotspot in CDR-H3 (the "n+1" strategy:
  change the residue after the labile Asp, not the Asp itself). Model-peptide data puts the
  succinimide/isomerisation rate drop at ~7-18x. Binding-neutral: every HER2 contact Gly103
  makes is through the backbone (incl. a 2.42 A H-bond to HER2 Lys593), so swapping the side
  chain preserves them. Predicted dTm ~ +0.7 C.
- T32Y — the main stabiliser. Thr32 is ~2% exposed; Tyr buries ~75 A2 of hydrophobic surface.
  Predicted dTm ~ +4 to +7 C on its own.
- Combined (T32Y + G103A): predicted Fab Tm ~87-90 C (up from ~82 C). The additive effect
  holds because the two sites are in different CDRs ~9 A apart. Whole-antibody Tm stays 71-72 C
  because it is rate-limited by CH2 unfolding in the Fc — not in the 1N8Z structure.

## 70% rule — direction confirmed
The session ran the check explicitly: the T32Y + G103A mutant is **100% identical to WT in
CDR-H3 (Kabat 95-102)** — G103A sits just outside that window, so it does not count against the
CDR-H3 score. It "clears the 70% threshold easily".

So the optimisation-track rule is: **keep CDR-H3 (Kabat 95-102) at least 70% identical to WT.**
This is the opposite direction from the de novo track (novel = <70%).

## What this means for our pipeline
- Two tracks, opposite directions on the same metric:
  - optimisation: CDR-H3 close to WT (>=70% identical)
  - de novo: CDR-H3 novel (<70%)
- Our paratope_identity filter currently marks anything >0.70 as "duplicate". Correct for
  de novo, **wrong for optimisation**. It needs a per-track setting before the trastuzumab
  reference is loaded. Not built yet.
- Candidate format would be fab (VH + VL), see spec 0.1.3.
- The predicted Tm/binding numbers, if ever filed, go in as predicted evidence with the model
  named (structure-based estimate from 1N8Z), never as measured Tm or affinity.

## Open questions
1. What is the "70%" measured against in the official hackathon rule (WT parent — which this
   session assumed — vs a sequence database)?
2. Confirm the mutation-count sign ("at most 4" vs "fewer than 4").
3. Does the bio teammate want his designs in the registry, or only the written deliverable?
