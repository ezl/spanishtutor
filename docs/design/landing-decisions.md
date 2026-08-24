# Landing page: decisions and open questions

Shipped 2026-08-22. The page itself is `learner/templates/learner/landing.html`
and `learner/static/learner/css/minispanish.css` — read those for what it *is*.
This file records why it is that way, and what is still unresolved.

The design was worked out on a Claude Design canvas whose sources lived in
`docs/design/landing/`. They were deleted once the page shipped: the format only
opens in an editor baked into a published artifact, regenerating it needs a
helper that is not in this repo, and a mockup kept beside working code becomes a
second source of truth that goes stale. Everything worth keeping is below.

## Structure

Nine sections, each carrying exactly one attribute, in this order:

1. Hero
2. Learn via chat
3. Five minutes is a lesson
4. Live correction, instant feedback
5. Component skills, novice to mastery
6. Brings back what's slipping
7. Lessons built from your life
8. Picks what to study for you
9. Meet Luz Ángela, then the close

**"How it works" was cut.** The old page had a 1-2-3 summary. Once every
attribute stands in its own section, that list only restates the page.

**Section order is deliberate.** "Five minutes is a lesson" sits third, before
any mechanics, because the name makes a promise the reader starts checking from
the first screen. Section 5 ends with "one component skill is about one lesson's
worth of work" — that line is what makes *mini* a teaching decision rather than
a convenience, and it is the antidote to reading "mini" as "won't get me
fluent". If you edit section 5, keep that bridge.

**Luz is last on purpose.** Users care what they get, not what the persona is
called. Her section leads with what having a teacher buys you; the name is a
detail inside it.

## Decisions

**No header, no nav.** The only way into the product is a chat. "How it works"
is answered by the page. A nav bar on a single-scroll page mostly gives people a
way to leave. The wordmark lives in the footer only.

**Every CTA opens the platform picker.** Dropping a non-Messenger visitor on
`m.me` is a silent dead end: they hit a login wall for an app they may not have,
and nothing on the page told them Discord exists. The extra click is worth it
because the failure it prevents is total. Logo leads, word supports.

**The one exception is the chips in "Learn via chat", which link straight
through.** The rule above exists for a generic "Start Learning" where the
visitor has not chosen. Clicking a labelled logo *is* the choice, so routing it
via the picker would ask a question already answered. WhatsApp stays a `span`
with no `href` until the transport exists; a test enforces that.

**Five CTAs, identical wording, each with its own `ref`.** Hero, three bands
after sections 4, 6 and 8, and the close. `web_hero` / `web_correction` /
`web_srs` / `web_picks` / `web_close`. Previously every signup logged as
`web_hero` and the page could not be read. A test enforces distinct refs.

**No pricing anywhere.** The old page promised "First 10 lessons free ·
$20/month · Cancel anytime" while testing is friends-only. `test_no_pricing_claims`
fails if a price or the word "free" reappears — that test is the reminder, not
an obstacle.

**No testimonials.** None exist yet. Fabricating them is the one thing that
would sink the page.

**WhatsApp is greyscale and inert.** There is no whatsapp transport, no
`PLATFORM_ID_FIELD` entry, no number. Greyscale against two brand-coloured logos
reads as "not yet" without needing a label.

**The skill grid is indicative, not a live readout.** Real skill names and the
five-state vocabulary come from `/progress/`, trimmed to the two shipped modes
because showing listening and speaking as "coming soon" advertises unbuilt work.
No legend: the colour ramp carries it.

## Visual direction

Instrument Serif over Karla. Coral `#f4623a`, teal `#34b8a8`, amber `#f5b32e`
chosen at a shared lightness so none dominates. Hand-drawn feel lives only in
the SVG wavy rules, never in the typeface. The CTA is a coral pill rather than a
hard-edged block; it repeats five times, so its shape sets more of the tone than
any other single element.

Rejected: a notebook direction (handwritten type, warm but reads childish and
ages fast) and a dark "night chat" direction (loud, but dark pages convert worse
with older and less technical audiences).

Tokens and component classes are in `minispanish.css` so `/privacy/` and
`/terms/` can adopt them; both still carry their own inline Inter styling.

## Competitive note

"Personalized to your interests" is **not** differentiating against the AI-tutor
cohort — Langua, Spanish Ai, LingoLenco and Jumpspeak all claim it in nearly
identical words. It *is* differentiating against Duolingo, Babbel and Busuu,
which personalize on performance rather than interests. Most visitors' reference
point is Duolingo, so the claim earns its place, but it is the half of the hero a
competitor could copy in an afternoon. The durable claims are "it lives in the
messaging app you already have" and "it measures component skills and shows you
the map" — neither cohort says either.

## Open

**Cross-platform accounts.** Per the no-account-linking decision in `CLAUDE.md`,
picking a second platform later silently creates a second `User` with a second
skill grid and no progress. The picker makes that choice look casual and
reversible. It is neither. Not designed for. Options range from a line in the
picker to remembering the choice and deep-linking returning visitors back to it.

**minispanish.com versus multi-language.** `PLAN.md` plans French and others,
down to `engine/personas/fr.py`. The domain forecloses that, or forces a second
brand later.

**A portrait for section 9.** There is no photograph of a persona who is not a
person. The section currently ships text-only. Illustration, abstract mark, or
nothing at all.

**Verified at 390px only.** No horizontal overflow, all hit targets at or above
44px, no text under 12px, tiles inline, grid intact, picker stacked. Tablet and
landscape deliberately unchecked.
