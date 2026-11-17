# Evidence-qualified security and surface-group mathematics (research v0)

**Scope: LOCAL RESEARCH CANDIDATE. NOT TRAINED, NOT DEPLOYED, NOT A PUBLIC BENCHMARK OR PROOF.**

This optional research folder does not alter frontier-bench's engine, retrieval, quantization or Hugging Face publisher; no results are inserted into public scoreboards, no credentials are read, and no external scanners or model providers are invoked.

## Two screenshot leads

Screenshot 1 shows a publicly disclosed Poindexter Labs mini-benchmark problem, "Block Picard Groups and Surface Bundles," and its reference answer. A reference answer printed next to the question cannot support a blind capability score and is not a derivation. The screenshot is not copied into the repo. The PDL-Bench dataset card warns that published answers contaminate training data and asks users not to train on its rows. This screen sample is not assumed to be identical to a PDL-Bench dataset row. Treat all public-answer Poindexter materials as evaluation-only and exclude them from training, preference optimization, model fine-tuning and retrieval-training corpora.

Screenshot 2 lists twelve GitHub projects. GitHub stars are not security certification, and names in a social post are not authoritative repository IDs. We independently retrieved canonical public GitHub metadata, exact default-branch source SHAs, licenses, selected security policies and releases into upstream-catalog.v1.json.

## Twelve distinct trust domains

| Upstream | Security capability | Admission posture |
| --- | --- | --- |
| usestrix/strix | autonomous, dynamically exercising penetration-test agent | no execution until specifically owned staging target/traffic approved |
| cloudflare/security-audit-skill | six-phase coverage-led code audit, independent candidate verification | reuse process; do not install unknown skill or execute target-controlled content |
| aquasecurity/trivy | dependency, image, SBOM, IaC and vulnerability scan | pinned local static profile, retain DB and coverage receipts |
| projectdiscovery/nuclei | template-driven active network/API scan | no live network targets; signed templates and owner approval |
| trufflesecurity/trufflehog | secret discovery and optional live credential verification | no verification or secret emission; AGPL needs legal review before redistribution |
| NVIDIA/skillspector | agent-skill static/optional AI review | pre-install offline/static trial, account for optional OSV/provider egress |
| semgrep/semgrep | source-pattern and taint-flow SAST | exact ruleset/engine and file coverage; project-specific guards |
| openai/codex-security | semantic threat-model-based code review | owner-approved private-source egress, cost, independent remediation tests |
| Cisco-Talos/clamav | signature-based file malware detection | untrusted artifact quarantine, signature freshness and negatives reported |
| anthropics/claude-code-security-review | model-assisted PR diff analysis | only as complementary PR review, not exploit proof; private code egress gated |
| qybaihe/bla | decoys / anti-reverse-engineering artifacts | reject on datasets, receipts, public models and evidence: risks deliberate poisoning |
| microsoft/PyRIT | model- and app-level adversarial red-team testing | synthetic own-model target only, separately approved target/budget |

Every upstream SHA is frozen for reproducibility in the catalog. Public license metadata is not clearance to incorporate or redistribute upstream code. No upstream code is vendored or executed.

## The mathematics we can verify

Let N_g be the closed nonorientable genus-g surface, the connected sum of g projective planes. Its fundamental group is presented by generators a_1,...,a_g subject to a_1^2 ... a_g^2 = 1.

For a **finite discrete** group G, principal G-bundle isomorphism classes over N_g correspond to simultaneous-conjugacy orbits of group homomorphisms. Burnside's lemma gives:

    b_g(G) = (1/|G|) sum_{h in G} #{(x_1,...,x_g) in C_G(h)^g : x_1^2 ... x_g^2 = 1}

A direct product of finite groups has factorized orbits:

    b_g(G_1 x ... x G_m) = b_g(G_1) ... b_g(G_m).

For finite abelian G, b_g(G) = |G|^(g-1) * |G[2]|.

For genus 5, two **independent** algorithms return C2=32, C3=81, C2 x C3=2592, S3=502, and Q8=4352. An additional mixed nonabelian check confirms S3 x C2 gives 16,064 via Burnside and the product of independently checked factor results. The group-table validators independently check identity, inverses and associativity. The two algorithms use explicit conjugacy representatives versus centralizer dynamic programming. Bounded test fixtures are original synthetic constructions, not copies from the benchmark.

### Third mathematical cross-check: character theory

For a finite G with irreducible representations of complex dimension d_χ
and Frobenius–Schur indicator ν_χ, Klug's closed-nonorientable formula gives

    |Hom(pi_1(N_g),G)| = |G|^(g-1) sum_χ ν_χ^g d_χ^(2-g).

This is checked with exact rational arithmetic, not floats, using separately
declared character fixtures for C2, C3, C4, S3 and Q8. For g=5:

- S3: irreducible degrees (1,1,2), all indicators +1,
  giving 2,754 homomorphisms into S3. The centralizers (S3 once,
  C2 three times, C3 twice) give (2,754 + 3*32 + 2*81)/6 = 502 orbits.
- Q8: four real one-dimensional irreps and a two-dimensional quaternionic
  irrep with indicator -1 give 15,872 homomorphisms into Q8.
  Centralizers (Q8 twice and C4 six times) give
  (2*15,872 + 6*512)/8 = 4,352 orbits.
- Independent direct explicit conjugacy enumeration and centralizer DP
  match both resulting orbit counts.

These known character dimensions/indicators and centralizer multiplicities
are declared inputs, not computed or kernel-checked by this candidate.
The third method does not prove that supplied character data are complete
or correct for an arbitrary G. It is another adversarial cross-check and
an explicit *proof obligation* for future automatic group adapters.

This is NOT a solution to the screenshot problem. It requires the exact group laws for the fundamental groups of the classifying spaces of the modular bimodule categories, including the block Picard/Morita groups. Their finite-group presentation, completeness and verified multiplication tables are not supplied. Without those, the benchmark result is UNAVAILABLE regardless of matching a published factorization.

Source: Michael R. Klug, Counting homomorphisms from surface groups to finite groups, Canadian Mathematical Bulletin, doi:10.4153/S0008439524000420.

Existing Lutar-lean F4 is Nat backward-DAG acyclicity, not this theorem. F7 is in-memory FIFO, not exactly-once runtime delivery. F22 is List.range monotonicity. Lambda uniqueness is not unconditional, and Lambda is neither a calibrated risk probability nor an authorization gate. No Lean kernel proof is asserted for this new research.

## Security pipeline, separated by evidence and authority

Stage A: source/head and rule-engine/DB version, scan coverage and incompleteness.
Stage B: raw candidate finding (UNTRUSTED, not a severity award).
Stage C: separate independent disproof/reproduction with source trace.
Stage D: externally verifiable witness and human security/protocol approval.
Stage E: release/rollback and public readback only after all other stages qualify.

Security proposals always return execution_authorized:false. Tool results cannot declare themselves confirmed: an unverified independent-reproduction assertion remains VALIDATION_CLAIM_UNVERIFIED. Missing trust evidence remains NEEDS_VALIDATION. No live secret verification, no active penetration test, no public model prompt egress.

## Future training experiment, not an executed run

Original bounded group-table tasks may become synthetic training candidates only after rights checks and independent exact labels. Split by group isomorphism class to avoid trivial near-duplicate leakage, and retain separate group-family holdouts. Score formal representation, quotient action, exact result, independent-check agreement, abstention, per-stage false verification rate, p95/cost and source provenance. GPU training, inference quality, and publication are NOT verified or authorized. Published-answer benchmarks remain quarantined, not used for teaching or validation of an allegedly blind pass@1.

## Execution and CI

Run from repository root:

    python -B -m unittest discover -s tests -p test_security_math_frontier.py -v

No third-party packages, network, scanner installations, or model calls. The existing pull-request bench-suite-publisher VERIFY job discovers all tests/test_*.py files; its PUBLISH job is main-only, not pull-request, so no benchmark Space write is triggered by this draft PR. No engine/retrieval/quantization receipts are modified.

**Admission gates**: CI at exact head, repo source/scope ownership review, security license review, human protocol approval, no private-code egress, and no merges/deployments/model publications without a separate approved release. A green check is not a permission receipt.
