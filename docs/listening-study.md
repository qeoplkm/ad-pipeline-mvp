# Listening study kit

This kit tests the question the prototype exists for: **is machine-written audio description, with no human review, useful to blind and low-vision viewers?** It compares three versions of the same clips:

| Condition | What it is |
|---|---|
| **A: Full** | Pipeline output at density 1.0 (narration may fill each dialogue gap) |
| **B: Sparse** | Pipeline output at density 0.5 (about half of each gap left silent) |
| **C: Professional** | Human-written AD from a streaming service or DVD, as the reference |

Run A and B on the same clip from the web UI (or `run.py --density 1.0` and `--density 0.5`). For C, pick clips that already have a professional AD track. Use the *Hide picture* player mode, or the `mixed.mp3` files, so sighted facilitators hear what participants hear.

Before recruiting: if this is done through a university or will be published, check whether your institution requires ethics review (IRB) for studies with human participants. Get recorded or written consent either way.

---

## 1. Recruitment post

Post through local chapters of the American Council of the Blind (ACB) or the National Federation of the Blind (NFB), university disability services offices, or communities such as r/Blind (read each community's rules on research posts first). Keep the post plain text so screen readers read it cleanly.

> **Paid listening study: help us test automatic audio description**
>
> We are building a tool that writes audio description for film and TV automatically. Before we build anything further, we want to hear from people who actually use audio description.
>
> **What you would do:** Listen to three short film clips, each 3 to 8 minutes, with different versions of audio description. After each one, we ask a few questions out loud. The whole session takes about 45 to 60 minutes, over video call or phone.
>
> **Who we're looking for:** Adults (18+) in the U.S. who are blind or have low vision and watch movies or TV with audio description at least now and then. No technical knowledge needed.
>
> **Thank you:** a $40 gift card for a full session.
>
> **Privacy:** We do not record your face. With your permission we record audio of the session for our notes, then delete it after transcription. Your name is never published.
>
> To take part, reply with your email or phone number and a few times that work for you. Questions are welcome.

Adjust the amount and logistics to your budget. Offering phone participation matters: not everyone is comfortable with video-call software.

---

## 2. Session script (about 50 minutes)

**Setup (5 min).** Thank them. Confirm consent and recording. Explain: "There are no right answers. We're testing the descriptions, not you. If something annoys you, that is exactly what we need to hear." Ask how they usually watch: which services, how often they use AD, and whether they use a screen reader.

**Clips (3 × 12 min).** Play one condition per clip, and **rotate the order between participants** so no version always goes first. Do not say which version is human-written. For each clip:

1. Play the clip once, without interrupting.
2. Ask the after-clip questions (section 3).
3. Optionally replay a 30-second moment they mentioned and ask what exactly worked or didn't.

**Wrap-up (5 min).** Ask the closing questions. Thank them and send the gift card.

Facilitator notes: keep your own reactions neutral, and write down exact quotes for strong reactions ("I had no idea who was talking", "it never shuts up").

---

## 3. Questionnaire

Ask questions aloud, or share them as a plain-text or accessible form. Scales are 1 to 5; say the anchors every time ("1 means not at all, 5 means completely").

### After each clip

1. In your own words, what happened in this clip? *(Free answer. Score it later against a short list of key events for that clip, prepared in advance.)*
2. How well could you follow what was happening? (1 = not at all, 5 = completely)
3. Did you always know **who** was on screen or doing something? (1 = never, 5 = always)
4. How often did the description talk over dialogue or important sounds? (1 = constantly, 5 = never)
5. How was the **amount** of description? (1 = far too little, 3 = about right, 5 = far too much)
6. Did any description seem **wrong**, or describe something you doubt was there? (yes / no / not sure. If yes: what?)
7. Did the description ever repeat what a character had just said, or give away something before it happened? (yes / no. If yes: when?)
8. How natural did the narrating voice sound? (1 = very robotic, 5 = very natural)
9. Overall, how satisfied were you with this description? (1 = very dissatisfied, 5 = very satisfied)

### At the end

10. Which of the three versions did you prefer, and why?
11. Would you watch a whole movie with description like your favorite of today's versions? (yes / maybe / no)
12. If a service only had automatic description like this for a title with no professional AD, would you rather have it or nothing? (rather have it / depends / rather have nothing)
13. What is the one thing that most needs to improve?

---

## 4. What to measure

| Question | Answers |
|---|---|
| Is it accurate enough? | Q6 "wrong" mentions per clip, and the key events recalled in Q1, compared with professional AD |
| Who is who? | Q3, and Q1 answers that confuse characters |
| Does it get in the way? | Q4, Q7 |
| How dense should it be? | Q5 for Full vs Sparse; Q10 preferences |
| Is it worth shipping without review? | Q11, Q12 |

Five to eight participants are enough to find the main problems. Look for patterns across people, not averages: if three of six say they couldn't tell who was speaking, that's the priority. Report full and sparse density separately; the answer to "how much should it say" decides the default `--density`.

Feed findings back into the pipeline:

- Character confusion: a better cast list, a consistency check, or naming characters more often.
- "Talks too much": a lower default density.
- "Talks over things": larger padding or transient guards.
- Factual errors: human review in the editor before release (the web UI's review table).
