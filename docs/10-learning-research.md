# Learning-science basis and limits

This note records the evidence used to shape Virtuoso. It is a design input, not a claim that the current CLI has proven better learning outcomes. Product effectiveness still requires dogfood data and, later, controlled comparisons.

## Supported design choices

### Test before teaching

Practice should require an attempted retrieval before revealing the answer. Meta-analyses report a reliable testing effect in laboratory and classroom settings, although effect size depends on test format, feedback, delay, and the final assessment.

Design consequence: the prompt appears before feedback; blank recall cannot be marked demonstrated; hints, open notes, and agent help stay attached to the attempt.

Sources:
- Yang et al. (2021), “Testing (quizzing) boosts classroom learning,” systematic review and meta-analysis. https://doi.org/10.1037/bul0000309
- Adesope, Trevisan, and Sundararajan (2017), “Rethinking the Use of Tests,” meta-analysis. https://doi.org/10.3102/0034654316689306

### Space retrieval and adapt cautiously

Distributed practice improves later recall, and the useful interval changes with the desired retention interval. Performance-adaptive scheduling is plausible and has encouraging large-scale observational and optimization evidence. That does not establish one algorithm as universally best.

Design consequence: scheduler algorithm, version, configuration, and learning context are stored with every proposal. FSRS is the atomic-recall baseline, not a competence model. Future meta-scheduling must compare forecast calibration, workload, retention, and transfer by context before changing defaults.

Sources:
- Cepeda et al. (2006), distributed-practice quantitative synthesis. https://doi.org/10.1037/0033-2909.132.3.354
- Cepeda et al. (2008), spacing as a function of retention interval. https://doi.org/10.1111/j.1467-9280.2008.02209.x
- Tabibian et al. (2019), adaptive spaced-repetition optimization with large-scale natural-experiment evidence. https://doi.org/10.1073/pnas.1815156116
- Ye et al. (2022), stochastic optimization for spaced-repetition scheduling. https://doi.org/10.1145/3534678.3539081

### Difficulty and latency are signals, not verdicts

Effortful successful retrieval can strengthen memory, but difficulty can also mean poor encoding, ambiguity, accessibility needs, fatigue, or a bad prompt. Response latency is useful for within-item trends and scheduler calibration, but it must not stand alone as evidence of capability.

Design consequence: Virtuoso records latency alongside correctness, confidence, support, and context. It does not reward speed pressure or treat a fast answer as mastery.

Source:
- Maddox and Balota (2015), retrieval practice, spacing, and desirable difficulty. https://doi.org/10.3758/s13421-014-0499-6

### Separate recall from transfer

A correct recall attempt does not prove that a learner can use the concept in a new situation. Project application should be recorded separately, with the exact item version, outcome, independence, assistance, artifact reference, and a delayed re-check.

Design consequence: `transfer record` creates append-only project-application evidence and a seven-day delayed-check date. It always records `claims_mastery: false`. Project completion and XP cannot silently upgrade capability.

### Calibration and mastery need repeated evidence

Mastery-learning programs have positive average effects, but a product must define mastery carefully. Metacognitive-monitoring interventions can improve calibration, yet confidence remains fallible.

Design consequence: future capability views should combine repeated delayed recall, varied problems, independent project transfer, and uncertainty. Confidence is retained for calibration analysis, never used alone.

Sources:
- Kulik, Kulik, and Bangert-Drowns (1990), mastery-learning meta-analysis. https://doi.org/10.3102/00346543060002265
- Gutierrez de Blume (2022), metacognitive-monitoring intervention meta-analysis. https://doi.org/10.1037/edu0000674

## Claims Virtuoso must not make yet

- That FSRS or any custom meta-scheduler is best for every learner or learning context.
- That shorter recall time always means stronger understanding.
- That one successful test, exercise, project, streak, level, or XP total proves mastery.
- That interleaving is always superior; benefits depend on material and task structure.
- That a project artifact proves independent performance without help attribution.
- That the current implementation improves long-term retention. The two-week pilot tests usability and evidence quality first.

## Evaluation path

1. Dogfood at least ten real sessions and three authentic project applications.
2. Measure administration time, scheduler overrides, forecast calibration, retention at delayed checks, assistance, and project transfer.
3. Compare scheduler policies within the same learning context before comparing across contexts.
4. Introduce a custom or ensemble meta-scheduler only when it beats a named baseline on held-out learner events without increasing workload or hiding uncertainty.
5. Keep personal learning events local; publish only synthetic fixtures and aggregated, consented findings.
