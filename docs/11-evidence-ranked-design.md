# Acadine Virtuoso — evidence-ranked design memo

**Evidence checked through 2026-08-19.** DOI metadata was verified against Crossref; key findings were checked against abstracts or full-text records where available.

**Evidence grades**

- **A — strong:** high-quality meta-analysis or convergent classroom and laboratory evidence.
- **B — credible but bounded:** strong experiment, field study, or authoritative synthesis with important scope limits.
- **C — promising:** narrow experiments, observational platform data, or maintainer-run engineering benchmarks.
- A minus sign marks evidence near the lower edge of a grade; paired grades such as **A/B** or **B/C** mean the evidence is mixed across those adjacent levels.

## Executive conclusion

The research strongly supports building Virtuoso around:

1. **Attempted retrieval before answer exposure**
2. **Corrective, explanatory feedback**
3. **Spacing personalized to the learner, item, and retention horizon**
4. **Interleaving when learners must discriminate among confusable strategies**
5. **Separate, delayed capability and transfer checks**
6. **Explicit confidence judgments and calibration feedback**

It does **not** support collapsing these into one “mastery” score or one scheduler.

Virtuoso should operate three distinct decision systems:

| System | Question | Legitimate evidence | Scheduling basis |
|---|---|---|---|
| **Memory scheduler** | “When is this atomic item likely to become hard to retrieve?” | Recall outcome, elapsed time, history, possibly normalized latency | Estimated retrievability and target retention |
| **Capability checker** | “Can the learner independently use this knowledge under relevant conditions?” | Attributed explanations, applications, projects, novel/delayed tasks, assistance level | Evidence gaps, stakes, uncertainty, transfer horizon |
| **Project prioritizer** | “What should the learner work on now?” | Goals, milestones, dependencies, urgency, opportunity value | Human/project priorities—not forgetting probability |

FSRS may propose the next **memory review**. It should not certify capability or decide which project matters.

---

## 1. Active recall and the testing effect — **A**

### Strongest evidence

- **Yang et al. (2021)** synthesized **222 classroom studies and 48,478 learners**. Quizzing improved academic achievement by **Hedges’ g = 0.499**, with effects moderated by feedback, repeated testing, test format, material alignment, and comparison condition.
  DOI: https://doi.org/10.1037/bul0000309

- **Rowland (2014)** found a robust retention advantage for testing over restudy across laboratory studies, generally stronger when retrieval required recall rather than mere recognition.
  DOI: https://doi.org/10.1037/a0037559

- **Pan & Rickard (2018)** examined **192 transfer effects from 122 experiments**. Retrieval practice produced transfer relative to restudy, **d = 0.40, 95% CI [0.31, 0.50]**, but transfer depended strongly on task congruence, elaboration, and initial performance. Publication-bias analyses often predicted no transfer when favorable moderators were absent.
  DOI: https://doi.org/10.1037/bul0000151

- **Chan et al. (2024)** manipulated retrieval-practice performance in six experiments and found that the magnitude of the testing effect was experimentally independent of practice performance. A positive person-level correlation was shown to be potentially artifactual.
  DOI: https://doi.org/10.1037/xge0001593

### Supports

- Make learners produce an answer, explanation, prediction, derivation, or solution **before** seeing the answer.
- Use retrieval repeatedly and with delays.
- Match retrieval format to the desired future use while also varying context when transfer is a goal.

### Does not support

- “Any quiz works.”
- Recognition-only cards being equivalent to generation.
- Maximizing practice-session accuracy.
- Assuming successful fact recall proves application skill or far transfer.

### Product implications

- Default to short-answer, explanation, code, diagram, or decision prompts; use multiple choice mainly for discrimination or diagnosis.
- Record whether an answer was independently generated, recognized, hinted, or revealed.
- Treat retrieval performance as a memory event—not an automatic capability pass.

---

## 2. Retrieval difficulty and response latency — **B/C**

### Evidence

- **Pyc & Rawson (2009)** experimentally varied spacing and learning criteria while retaining successfully retrieved items. More difficult **successful** retrieval tended to improve final retention, with diminishing returns.
  DOI: https://doi.org/10.1016/j.jml.2009.01.004

- **Maddox & Balota (2015)** used acquisition response latency as a retrieval-difficulty proxy and found results broadly consistent with desirable-difficulty accounts in younger and older adults. Latency required within-person standardization because baseline speed differed substantially.
  DOI: https://doi.org/10.3758/s13421-014-0499-6
  Full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC4480221/

- **Mettler, Massey & Kellman (2016)** reported two experiments in which adaptive scheduling based on response time and accuracy outperformed fixed—including yoked—practice schedules on immediate and delayed retention.
  DOI: https://doi.org/10.1037/xge0000170

### Supports

- Latency can add information about current accessibility beyond binary correctness.
- Some effort during a successful retrieval is beneficial.
- Adaptive scheduling can use performance dynamics rather than fixed intervals.

### Does not support

- “Slower is always better” or “slower always means schedule sooner.”
- Maximizing difficulty or failure.
- Treating raw latency as comparable across learners, devices, modalities, answer lengths, or motor demands.
- Treating fast recall as proof of practical capability.

### Product implications

Capture latency, but initially use it as an **observational feature**, not a deterministic grade:

- Normalize within learner, response modality, prompt type, and approximate answer length.
- Separate thinking time from typing time where feasible.
- Fit latency against **future recall**, not intuition.
- Preserve the raw event so future models can be evaluated retrospectively.
- Do not silently modify FSRS ratings from latency until Virtuoso-specific held-out evidence shows better calibration or review efficiency.

---

## 3. Spacing and FSRS-style scheduling — **A for spacing; C for current FSRS superiority**

### Spacing evidence

- **Cepeda et al. (2006)** synthesized **839 assessments from 317 experiments**. Spacing reliably aided retention, and the best interstudy interval increased with the desired retention interval.
  DOI: https://doi.org/10.1037/0033-2909.132.3.354

- **Cepeda et al. (2008)** studied more than **1,350 participants**, gaps up to 3.5 months, and final tests up to one year. Performance followed an inverted-U relation: the best gap depended on the final retention horizon—roughly 20–40% of a one-week delay but 5–10% of a one-year delay in that fact-learning paradigm.
  DOI: https://doi.org/10.1111/j.1467-9280.2008.02209.x

- **Lindsey et al. (2014)** embedded personalized review in a semester-long middle-school language course. On a cumulative post-semester exam, personalized review improved retention by **16.5% over massed practice** and **10.0% over one-size-fits-all spacing**, under time-matched comparisons.
  DOI: https://doi.org/10.1177/0956797613504302

### Algorithmic evidence

- **Settles & Meeder (2016)** reported that Duolingo’s half-life regression reduced recall-prediction error by more than 45% versus tested baselines and improved daily engagement by 12% in an operational study. Prediction and engagement are not retained capability.
  DOI: https://doi.org/10.18653/v1/P16-1174

- **Tabibian et al. (2019)** developed the MEMORIZE optimal-control formulation and reported favorable associations in large Duolingo observational data. The authors explicitly noted that the natural experiment could not make direct interventions; the observed window was also short.
  DOI: https://doi.org/10.1073/pnas.1815156116

- A related large-scale scheduling algorithm was reported by **Ye, Su & Cao (2022)**, but it is not a validation of present FSRS versions.
  DOI: https://doi.org/10.1145/3534678.3539081

- The current open FSRS benchmark reports results over roughly **10,000 Anki users and hundreds of millions of review records**. Its current tables show newer FSRS versions improving over older FSRS variants, while some higher-capacity sequence models predict held-out reviews better. This is useful engineering evidence, but it is maintainer-run, non-randomized, and evaluates prediction rather than causal learning efficiency.
  Benchmark snapshot: https://github.com/open-spaced-repetition/srs-benchmark/tree/1053082bd2d6dbedbbd9674c4c9683c203f6818a

### Supports

- Personalized, item-sensitive scheduling.
- Explicit retention targets and horizons.
- Evaluating schedulers through held-out future-recall calibration and review cost.
- FSRS as a compact, inspectable starting model.

### Does not support

- One universally optimal interval ratio.
- Current FSRS being independently proven superior for all learners or materials.
- Predictive log loss proving causal educational benefit.
- Using FSRS retrievability as “mastery,” capability, or project urgency.

### Product implications

- Present FSRS output as a **versioned proposal**, with user override and visible assumptions.
- Store model version, parameters, desired retention, proposed interval, accepted interval, and subsequent outcome.
- Backtest on held-out future events and compare models at equal review budgets or equal target retention.
- Label meta-spaced-repetition policies separately: a due date for revisiting a strategy or capability claim is not necessarily a memory-retrievability estimate.

---

## 4. Interleaving — **A-, strongly conditional**

- **Brunmair & Richter (2019)** synthesized **59 studies, 238 effects, and 158 samples**. The overall interleaving effect was **g = 0.42**, but it varied markedly: positive for paintings and mathematics, nonsignificant in some expository/taste tasks, and negative for word learning. Benefits were larger when categories were similar to each other, examples within a category were distinguishable, and material was complex.
  DOI: https://doi.org/10.1037/bul0000209

- **Rohrer, Dedrick & Stershic (2015)** followed 126 seventh-grade learners for three months. Interleaved mathematics practice improved unannounced tests after one and 30 days, **d = 0.42 and 0.79**, respectively.
  DOI: https://doi.org/10.1037/edu0000001

### Product implication

Interleave when the learner must answer **“Which method applies?”**, especially among confusable concepts. Do not randomly mix everything. Permit initial blocked practice for establishing a procedure, then introduce interleaved discrimination.

---

## 5. Feedback — **A**

- **Wisniewski, Zierer & Hattie (2020)** analyzed **435 studies, 994 effects, and more than 61,000 learners**. Overall feedback effect was **d = 0.48**, with substantial heterogeneity; information-rich cognitive and motor feedback was stronger than generic motivational or behavioral feedback.
  DOI: https://doi.org/10.3389/fpsyg.2019.03087

- **Van der Kleij, Feskens & Eggen (2015)** synthesized 40 computer-based studies. Elaborated feedback produced an effect of **0.49**, correct-answer feedback **0.32**, and correctness-only feedback **0.05**. Elaborated feedback was especially useful for higher-order outcomes.
  DOI: https://doi.org/10.3102/0034654314564881

- Multiple-choice testing can expose learners to plausible wrong answers; feedback reduces this risk.
  **Butler & Roediger (2008):** https://doi.org/10.3758/mc.36.3.604

### Product implications

After a committed response:

1. State correctness.
2. Show the correct answer or acceptable range.
3. Explain the decisive principle.
4. Diagnose the error where possible.
5. Ask for a corrected response or later reattempt.

Avoid praise-only feedback. Provide the first correction promptly; spacing should generally delay the **reattempt**, not withhold the correction.

---

## 6. Mastery learning — **B**

- **Kulik, Kulik & Bangert-Drowns (1990)** synthesized 108 controlled evaluations. Mastery-learning programs generally improved examination performance, particularly for weaker learners, but increased time on task and sometimes reduced completion in self-paced college formats. Effects varied with procedure, design, and subject.
  DOI: https://doi.org/10.3102/00346543060002265

- **Sinha & Kapur (2021)** synthesized 53 studies and 166 comparisons. Problem solving before instruction outperformed instruction-first designs overall, **g = 0.36, 95% CI [0.20, 0.51]**, especially when Productive Failure principles were implemented faithfully. Results reversed for some younger learners and domain-general skills.
  DOI: https://doi.org/10.3102/00346543211019105

### What this supports

- Correction, additional opportunity, reassessment, and flexible time.
- Attempt or prediction before explanation in appropriate domains.
- Progression rules tied to consequential evidence.

### What it does not support

- A universal 80% or 90% cut score.
- A single latent “mastery percentage.”
- Letting novices flounder without timely instruction.
- Treating one successful card review as mastery.

### Product implication

Represent progression as an explicit policy, for example:

> proceed after two independent retrievals plus one unassisted application; schedule a delayed transfer check.

The threshold should vary by consequence and be validated by whether it predicts later success.

---

## 7. Transfer and real projects — **A-/B**

- Pan & Rickard’s transfer meta-analysis found a mean **d = 0.40**, but transfer was conditional and weak or absent for several task changes.
  DOI: https://doi.org/10.1037/bul0000151

- **Barnett & Ceci (2002)** showed that “far transfer” spans multiple dimensions—knowledge domain, physical and social context, temporal distance, functional purpose, and response mode. A single near/far label conceals these distinctions.
  DOI: https://doi.org/10.1037/0033-2909.128.4.612

- **Blume et al. (2010)** synthesized 89 training-transfer studies. Transfer related to ability, motivation, conscientiousness, and supportive work environments; same-source, same-context outcome measurement systematically inflated relationships.
  DOI: https://doi.org/10.1177/0149206309352880

### Product implications

Projects are excellent **transfer tests and learning environments**, but artifact existence is not learner evidence. A project event should record:

- target capability;
- learner prediction or plan before assistance;
- authentic deliverable;
- acceptance criteria and scorer;
- assistance level and agent contribution;
- explanation or teach-back;
- later performance on a changed or novel case.

Keep **learning evidence** separate from **release evidence**. Agent-generated code passing tests proves something about the artifact; it proves learner capability only if attribution and independent performance are demonstrated.

---

## 8. Adaptive testing — **B for measurement; insufficient by itself for learning**

- **Chang (2015)** reviews the psychometric foundations and implementation problems of computerized adaptive testing (CAT), including item selection, large-sample foundations, and individualized assessment.
  DOI: https://doi.org/10.1007/s11336-014-9401-5

CAT chooses items to estimate ability efficiently. Spaced practice chooses activities intended to change memory. These are different optimization problems.

### Product implications

Until Virtuoso has a calibrated item bank and validated construct model, call its behavior **adaptive routing**, not psychometric CAT. Full CAT claims require:

- calibrated item parameters;
- evidence that items measure the intended construct;
- content balancing and item-exposure controls;
- a defined precision-based stopping rule;
- monitoring for model misfit and subgroup bias.

An item may be highly informative for assessment while being pedagogically undesirable, and vice versa.

---

## 9. Metacognitive calibration — **A-/B**

- **Gutierrez de Blume (2022)** synthesized 56 effect sizes and 7,667 participants. Learning-strategy instruction meaningfully reduced monitoring error, **g = −0.565** where negative denotes lower calibration error. Effects varied by judgment and context.
  DOI: https://doi.org/10.1037/edu0000674

- **Janssen & Lazonder (2024)** synthesized 35 problem-solving studies. Monitoring interventions had a small positive overall effect, **g = 0.25**. Whole-task interventions, metacognitive knowledge, and external standards helped; interventions focused merely on judgment timing were counterproductive in that literature.
  DOI: https://doi.org/10.1007/s10648-024-09936-4

- **Dunlosky & Rawson (2012)** experimentally showed that inaccurate self-evaluation can cause premature stopping and poorer retention.
  DOI: https://doi.org/10.1016/j.learninstruc.2011.08.003

### Product implications

- Ask for confidence **before** correctness or feedback is revealed.
- Prefer probability judgments, e.g. “70%,” over vague labels.
- Report calibration and discrimination separately: calibration curves/Brier-type scores versus whether confidence ranks correct above incorrect answers.
- Give feedback against external standards and encourage post-task diagnosis.
- Never use confidence alone as capability evidence or present poor calibration as a fixed learner trait.

---

## Recommended Virtuoso evidence model

Use an append-only `evidence_event` containing at least:

- learner, target, prompt and task type;
- timestamp and elapsed interval;
- response, correctness/rubric result;
- raw and normalized latency;
- confidence recorded before feedback;
- hints, tools, agent assistance, and answer exposure;
- context and modality;
- scorer and acceptance evidence;
- scheduler/model version;
- whether the event tests recall, application, independent transfer, or project delivery.

A defensible capability ladder is:

> unknown → exposed → retrieved → guided application → independent application → delayed/novel transfer

Only the first retrieval-oriented transitions belong naturally to FSRS.

## Highest-priority product decisions

1. **Ship answer-first retrieval plus elaborated feedback.**
2. **Use FSRS only for atomic memory items and expose it as a proposal.**
3. **Capture latency and confidence now; defer strong adaptive use until validated.**
4. **Add separate attributed application and delayed-transfer evidence.**
5. **Interleave confusable alternatives, not arbitrary content.**
6. **Define mastery gates as transparent evidence policies rather than percentages.**
7. **Keep project prioritization human- and goal-led.**
8. **Evaluate scheduler changes on held-out future recall, review cost, calibration, and delayed capability—not engagement alone.**

### Research audit

- **Repository documentation changed by this research commit:** `docs/10-learning-research.md` and this memo.
- **External changes:** none; source research was read-only and did not modify vaults, learner data, or remote systems.
- **Main limitation:** robust evidence exists for testing and spacing, but I found no independent randomized validation of the current FSRS versions themselves. Current FSRS evidence should therefore be presented as strong open engineering work, not settled causal educational science.
