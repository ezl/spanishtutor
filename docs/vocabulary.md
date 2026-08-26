# Vocabulary: research, theory, and design

Why the vocabulary system is shaped the way it is. Written 2026-08-26, when
vocabulary was still 21 one-line skill descriptions and no words existed
anywhere in the database.

> Citations below are from model knowledge, not freshly retrieved. The findings
> are well known and the direction of each is reliable, but spot-check any
> specific figure before it becomes load-bearing.

## The gap this replaces

Vocabulary used to be 21 entries in `skills.yaml`, each a single line of prose
(`a1_vocab_food_drink` was `"Common foods, drinks, meals (desayuno, almuerzo,
cena); ordering basics; me gusta/no me gusta"`). The model invented the actual
words at lesson time. Three consequences:

1. **No curation.** "Which words does a learner truly need" was answered fresh
   on every run, differently each time.
2. **No recurrence.** Nothing recorded which words were taught, so nothing could
   bring them back. SRS reviewed the *skill*, never the words.
3. **No personalization.** `user.interests` reached the lesson prompt as flavor
   for grammar examples. Nothing turned "goes to the gym" into taught gym words.

All three are the same missing thing: words were never first-class objects.

## Research basis

### Semantic clustering interferes; thematic clustering helps

The single most decision-shaping finding here.

Presenting several **semantically clustered** words together — same part of
speech, same category, mutually substitutable in one sentence slot (*tall,
short, fat, thin, blonde, dark*) — measurably slows learning and causes
cross-association: the learner retains that a word meant some body-size thing
but attaches the wrong one.

- Tinkham (1993), *System* 21(3) — semantic clusters learned more slowly than
  unrelated words.
- Waring (1997), *System* 25(2) — replication, same direction.
- Erten & Tekin (2008), *System* 36(3) — semantically related sets harder than
  unrelated sets.
- Nation, *Learning Vocabulary in Another Language* — recommends against
  teaching semantic sets together.

**Thematic** clusters behave oppositely: words tied by scenario rather than
category, mixed parts of speech (*gimnasio, pesas, sudar, cansado, levantar*).
Tinkham (1997), *Second Language Research* 13(2), found thematic clusters beat
both semantic clusters and unrelated words. They don't compete for one slot, so
there is nothing to cross-associate.

Not unanimous — Papathanasiou (2009), *ELT Journal* 63(4), found some benefit
to semantic presentation, mediated by learner level and word familiarity. The
interference direction is well replicated but the effect is not absolute.

**Design consequence:** packs are thematic, never taxonomic. A pack is a
situation, not a category.

### Contrast is a retrieval aid, not an encoding aid

The intuition that you may as well learn *short* while learning *tall* is
sound about the eventual goal and wrong about the timing — that pairing is
exactly the interference case above.

Teach one member, let it become secure, then introduce the opposite and
contrast them explicitly. This is only schedulable because words carry per-user
state: "introduce the antonym now that the base word is solid" is a query.

**Design consequence:** `LexItem.contrast_with` exists, and firing it is gated
on the base word's strength. It never fires at first encounter.

### Beginners learn formulas before they learn syntax

Stage 1 of Processability Theory (Pienemann; Johnston for Spanish) is single
words and memorized formulas — no syntax at all. Formulaic-language work
(Wray, *Formulaic Language and the Lexicon*; Nattinger & DeCarrico, *Lexical
Phrases and Language Teaching*) says the same: early learners store multi-word
chunks whole and only later decompose them.

`¿Cómo te llamas?` is one unit to a beginner, not *cómo* + *te* + *llamar* 2sg.
Teaching it as three analyzable pieces is teaching Stage 3 grammar to a Stage 1
learner.

We already use Processability Theory to justify the grammar sequence, so
honoring its Stage 1 is continuity, not a new commitment.

**Design consequence:** a lexical item may be a multi-word chunk. The earliest
A1 content is chunks, taught unanalyzed and explicitly labeled as such.

### Frequency, and why a raw frequency list is a trap

A small number of lemmas covers a large share of running text; the top hundred
or so Spanish words account for roughly half of all tokens (Davies, *A
Frequency Dictionary of Spanish*). That argues for frequency-ordered teaching.

But the top of the list is grammatical glue — *de, que, y, a, en, un, ser,
haber, no, por, con, su, para*. These are unteachable as a vocabulary list and
are better acquired through the grammar that uses them.

The useful reading: among words that are *learnable as vocabulary*, prefer the
frequent ones, and let the function words arrive through grammar.

**Design consequence:** core packs are frequency-informed, not frequency-ordered.
Curation is by hand — this is the step that keeps out nations-of-the-world and
list-every-religion bloat, and nothing automates it.

### Cognates are the largest free win available to an English speaker

A large share of English vocabulary has a Spanish cognate, concentrated in
Latinate and academic registers (estimates vary widely by counting method, so no
figure is quoted here). Regular patterns convert passive recognition into usable
vocabulary wholesale: `-tion → -ción`, `-ty → -dad`, `-ly → -mente`,
`-ous → -oso`, `-ment → -mento`.

A true beginner already recognizes hundreds of Spanish words they have never
studied. No other single lesson in the curriculum has that leverage.

Two limits keep it from being the opener, though. Cognate knowledge is
**receptive** — it lets a learner recognize words, not say anything. And the
word "cognate" is jargon that loses an ordinary person instantly.

**Design consequence:** the pattern lesson lands shortly after the opening
chunks, once there is real text to apply it to, and is never called "cognates"
to the student — it is framed as discovering the Spanish they already know.
False cognates are a separate, much later skill; they currently appear only at
C1, which is correct for *false* friends and was mistaken as coverage of true
ones.

### The first lesson is a phrase someone can say

Everything above is about efficiency. The opening lesson is judged on a
different axis: whether a person can say something real, to another person,
within five minutes of starting.

That rules out any pattern or strategy lesson, however high its leverage.
Recognition is not production, and a beginner who can decode fifty words but
cannot introduce themselves has not started speaking Spanish. Formulaic chunks
are the only thing that clears the bar on day one — `me llamo…`, `¿cómo estás?`
— and they are also what PT Stage 1 and the formulaic-language work say
beginners actually acquire first. The motivational argument and the acquisition
evidence point the same direction here.

**Design consequence:** A1 opens with productive chunks. Everything with a
better long-run payoff waits until the learner has said something.

### Teach the words that raise the input ceiling

Comprehensible-input arguments (Krashen, and input-processing work generally)
imply a specific practical ordering for a Spanish-first tutor: the highest-value
early words are the ones that let more of the lesson happen in Spanish.

*Sí, no, bien, mal, otra vez, más despacio, no entiendo, ¿cómo se dice…?,
¿qué significa?* Every one of these that lands means Luz can use more Spanish
and less English in every subsequent lesson. The payoff compounds in a way no
other vocabulary does.

**Design consequence:** classroom-operator vocabulary is taught second, right
after cognates, before anything topical.

### Telegraphic speech is a stage, not an error

"Yo ir escuela" is not a random mistake. It is the **Basic Variety** — the
system untutored adult learners spontaneously converge on across languages and
language backgrounds (Klein & Perdue, European Science Foundation project):
uninflected verbs, no copula, no functional morphology, meaning carried by
content words and word order. It is also Processability Theory Stage 2, the
stage immediately following the formulas of Stage 1.

Pienemann's **Teachability Hypothesis** holds that instruction succeeds when it
targets the learner's next developmental stage and fails when it skips ahead.
Teaching toward telegraphic production is therefore teaching exactly where a
post-chunk beginner is ready to be, and the communicative payoff is immediate:
at lesson ten they can say something true about their own life.

The risk is real and worth stating plainly. A substantial minority of Klein &
Perdue's learners **stalled at the Basic Variety permanently**. Deliberately
teaching a stage learners normally pass through could entrench it, and an
automatized form has to be overwritten later rather than simply filled in.

What makes the risk acceptable here is what those learners lacked: instruction
and corrective feedback. We have both, continuously. Three guardrails carry it:

1. **Name it as scaffolding.** The student is told plainly that this is how they
   will speak this week and that correct forms are coming. Learners who know a
   form is provisional do not cement it.
2. **Recast, never correct-and-stop.** The student says *ella comer taco*; Luz
   answers *sí, ella come tacos* and moves on. The correct form is always in the
   input even while the telegraphic form is accepted.
3. **Pair every infinitive with its *yo* form on introduction** —
   *hablar/hablo*, *comer/como*. Costs nothing and means the anchor already
   exists when present tense arrives.

Irregular verbs are kept out of this entirely. *Ir* is the most useful and the
most irregular verb in the language, so it is taught as the fixed chunk *voy a*
rather than as an infinitive — the student gets the useful form without ever
meeting the paradigm.

### Spacing and retrieval

Standard and uncontroversial: spaced retrieval beats massed exposure, and
retrieval beats recognition. Already the basis of the skill-level SRS.

Applied to words with one adjustment — a word does not need a quiz question to
count as practiced. Meeting it in context and understanding it is a real rep.
Producing it is worth substantially more than seeing it.

**Design consequence:** per-word scheduling is exposure-driven, lighter than the
skill SRS, and weights production above recognition.

## Design decisions

| Decision | Basis |
|---|---|
| Words are first-class rows, not prose in a skill description | All three gaps above |
| Packs are thematic (a situation), never taxonomic (a category) | Tinkham 1993/1997, Waring, Erten & Tekin |
| Antonyms are staggered, gated on the base word's strength | Same, plus retrieval-practice work |
| Lexical items may be multi-word chunks | PT Stage 1, Wray |
| Core packs curated by hand, frequency-informed | Davies; the anti-bloat requirement |
| A1 opens with chunks a learner can say on day one | PT Stage 1, Wray; production-first |
| Cognate patterns early but not first, never named "cognates" | Cognate leverage; plain-language constraint |
| Classroom operators taught second | Input ceiling compounding |
| Vocabulary is injected into *every* lesson, grammar included | Recurrence gap |
| Dedicated vocabulary lessons at A1 only | See below |
| Per-word scheduling is exposure-driven; production > recognition | Retrieval practice |
| Telegraphic production taught deliberately, with three guardrails | Basic Variety, Teachability Hypothesis |
| Irregular verbs enter as fixed chunks (*voy a*), never as paradigms | Same |
| Numbers split on Spanish morphology, not round English boundaries | Bite-size lessons; Spanish number formation |

### Why dedicated vocabulary lessons survive at A1 and nowhere else

Above A1 the student has enough grammar that words can ride along inside
grammar lessons, and drip-feed beats a word-list lesson. At A1 there is barely
any grammar to hang words on yet, and a true beginner genuinely needs a starting
stock before anything else can function. "Learn these twelve words" is a
legitimate and satisfying five minutes for someone at zero and a waste of one at
B1.

## The A1 opening sequence

The order this all produces, replacing what used to be greetings → numbers 1-1000
→ dates:

1. **Introduce yourself** — *hola, me llamo ___, ¿cómo te llamas?* Whole chunks.
2. **Ask how someone is** — the first two-turn exchange.
3. **Keep the conversation going** — *no entiendo, ¿cómo se dice…?* Raises the
   input ceiling for every lesson after it.
4. **Want and have** — *quiero ___, tengo ___, me gusta ___.* Fixed openers.
5. **Go and need** — *voy a ___, necesito ___, hay ___.*
6. **Spanish you already know** — the cognate patterns, never named that.
7. **People words** — *yo, tú, él, ella, nosotros, ellos.*
8. **Action words** — infinitives paired with their *yo* forms.
9. **Place words** — drawn from the student's own interests where possible.
10. **Putting words together** — deliberate telegraphic speech, scaffolded.
11-15. **Numbers**, in five lessons: 1-5, 6-10, 11-15, 16-20, 21-100.
16. **Dates and time**, which needed numbers first and previously came second.

Lessons 1-5 are pure formulaic chunks with no grammar at all. The first
analyzed grammar a student meets is noun gender, at lesson 17.

## Data model

- **`Pack`** — theme, level, and an `owner` that is null for the shared core and
  set for a generated interest pack. Provenance lives here.
- **`LexItem`** — Spanish lemma or chunk, English gloss, part of speech,
  gender/irregularity notes, optional `contrast_with` pointing at its opposite.
- **`UserWord`** — user × item: state, first taught, times seen, times produced,
  last seen, next due.

Interest packs are ordinary `Pack` rows with an owner. They are not a parallel
system, and a personal word is scheduled by the same rules as a core word.

## Consequences worth knowing

**The grid changes meaning.** With vocabulary leaving the skill queue above A1,
the 21 vocab skills stop being skill × mode cells. Vocabulary gets measured
per-word — exposure counts and due dates — instead of as a single 0–4 score for
"food vocabulary." Arguably better, but it is a real change to what the grid
represents.

**Session close gets more expensive.** Recording which target words appeared,
and whether the student produced or merely saw them, is a third extraction pass
alongside interests and feedback. Fold it into the existing call — three LLM
round-trips at session close is latency the student feels.

**A true zero needs an English-heavy mode.** The persona's Spanish-first rule
produces an incomprehensible wall for someone at absolute zero. That is a
persona and prompt problem, not a curriculum one, but the beginner path does not
work until it is solved.

## Open questions

- New-words-per-lesson budget. Two is safe and slow; five is fast and leaky.
- Whether a word ever "graduates" out of scheduling, or decays indefinitely.
- Whether interest packs are generated once at onboarding or regenerated as
  interests accumulate.
