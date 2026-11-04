# Spike S1-2 — EUR-Lex source format and corpus scope

Date: 2026-11-04 · Timebox 2 h · Issue #5

## Question
Which EUR-Lex documents and format give reliable article / paragraph / recital / annex units, in EN and DE, at the current legal state?

## Findings per document

| Doc | Text used | CELEX | Version / revision | Formats offered | EN | DE | Notes |
|---|---|---|---|---|---|---|---|
| GDPR | articles, annexes | 02016R0679 | 2016-05-04 · EN rev 000.002 (C1) · DE rev 000.003 (C1–C3) | HTML, PDF | ✅ | ✅ | revision differs per language |
| GDPR | recitals (173) | 32016R0679 | original OJ L 119, 4.5.2016 | HTML, PDF, OJ | ✅ | ✅ | consolidated text has no recitals |
| AI Act | articles, annexes | 02024R1689 | 2026-07-27 · EN rev 001.001 (M1) · DE rev 001.002 (M1, C1 2025, C2 2026-09-29) | HTML, PDF | ✅ | ✅ | includes Omnibus (M1); 75 ▼M1 blocks; Art. 113 shows 2 Dec 2027 / 2 Aug 2028 |
| AI Act | recitals (180) | 32024R1689 | original OJ L 2024/1689, 12.7.2024 | HTML, PDF, OJ | ✅ | ✅ | |
| DORA | articles + recitals (106) | 32022R2554 | original OJ L 333, 27.12.2022 | HTML, PDF, OJ | ✅ | ✅ | **no consolidated version exists** — original is current |
| AI Omnibus | amendments + recitals (47) | 32026R1744 | original OJ L 2026/1744, 24.7.2026 | HTML, PDF, OJ | ✅ | ✅ | written as amendment instructions ("erhält folgende Fassung") — context for "what changed / why", not main source |

## Structural findings (input for S1-4 parser)

1. **Consolidated texts contain no recitals.** Title is followed directly by Chapter I. Recitals come from the original acts; they are never amended.
2. **Revisions differ per language.** Same CELEX and date, different revision and corrigenda (GDPR EN 000.002 vs DE 000.003; AI Act EN 001.001 vs DE 001.002). The DE AI Act already contains a corrigendum to the Omnibus (C2, 29.9.2026) that EN does not. → Manifest stores `revision` per language; freshness check compares revision, not only date.
3. **Inserted articles.** AI Act consolidated has 113 numbered articles plus **4a, 60a, 75a, 75b, 75c, 75d** (119 article units). → `article` is a string; sort with a natural key.
4. **Markers to strip, keep as metadata:**
   - block markers `▼B`, `▼M1`, `▼C1` (source of the following block) → `amended_by` per paragraph
   - inline spans `►C1 … ◄` (corrected text inside a paragraph)
   - deleted provisions: `▼M1` followed by `__________` → drop the unit
5. **PDF noise.** Running header on every page (`02024R1689 — EN — 27.07.2026 — 001.001 — 128`), soft hyphens (`Regu­lation`), two different OJ layouts (old `L 333/1` vs act-by-act `Reihe L 2024/1689`).
6. **XML.** No Formex/XML is offered in the EUR-Lex UI for these documents. Cellar may hold Formex for original acts, but not consolidated texts — not pursued within the timebox.

## Expected unit counts (acceptance check for S1-4)

| Regulation | Articles | Recitals | Annexes |
|---|---|---|---|
| GDPR | 99 | 173 | 0 |
| AI Act | 119 (113 + 6 inserted) | 180 | 13 (I–XIII) |
| DORA | 64 | 106 | 0 |
| Omnibus | 7 | 47 | — |

## HTML markup (GDPR Art. 33, consolidated EN, inspected 2026-11-04)

```html
<div class="eli-subdivision" id="art_33">
  <p class="title-article-norm">Article 33</p>
  <div class="eli-title" id="art_33.tit_1">
    <p class="stitle-article-norm">Notification of a personal data breach to the supervisory authority</p>
  </div>
  <div class="norm">
    <span class="no-parag">1.&nbsp;&nbsp;</span>
    <div class="norm inline-element">…</div>
  </div>
  <div class="norm">…</div>   <!-- paragraphs 2–5 -->
</div>
```

- Article: `div.eli-subdivision[id^="art_"]`; number from the id (`art_33`, `art_4a`, `art_75b`), not from text.
- Title: `div.eli-title > p.stitle-article-norm`.
- Paragraph: each `div.norm` that is a **direct child** of the article div; number from `span.no-parag` (strip `&nbsp;` and `.`). Paragraphs have **no id**. Art. 33 has 5 such blocks = 5 paragraphs ✓.
- Points (a), (b), … are nested inside the paragraph text and stay in the paragraph's chunk.
- Original OJ HTML (recitals, DORA, Omnibus) uses a different markup (`oj-` classes) → second parser variant, one fixture each in S1-4.

## Recommendation
**Ingest HTML and parse with BeautifulSoup on the ELI structure**, not PDF: element ids give article numbers directly, no page headers, no soft hyphens, no layout differences between OJ series. PDF stays as a human reference only. Two parser variants: consolidated (ELI classes) and original OJ (`oj-` classes).

## Corpus manifest consequence
12 entries: 6 sources × 2 languages — GDPR consolidated, GDPR original (recitals), AI Act consolidated, AI Act original (recitals), DORA original, Omnibus original.
