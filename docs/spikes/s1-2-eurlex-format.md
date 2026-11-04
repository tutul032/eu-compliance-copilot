# Spike S1-2 — EUR-Lex source format (timebox 2 h)

Question: which format can be split reliably into articles, paragraphs, recitals and annexes, EN + DE, current consolidated versions?

## Findings per document
| Doc | CELEX | Consolidated version (CELEX + date) | Formats offered | EN | DE | Notes |
|---|---|---|---|---|---|---|
| GDPR | 32016R0679 | 02016R0679-20160504; EN rev 000.002 (C1), DE rev 000.003 (C1–C3) | HTML, PDF | ✅ | ✅ | revision differs per language; inline ►C1…◄ spans; consolidated text has no recitals → recitals from original 32016R0679 |
| AI Act | 32024R1689 | 02024R1689-20260727, includes M1 = Reg. 2026/1744; EN rev 001.001, DE rev 001.002 (M1, C1 2025, C2 2026-09-29) | HTML, PDF | ✅ | ✅ | 119 articles (113 + 4a, 60a, 75a–75d); Annexes I–XIII; 75 ▼M1 blocks; Art. 113 shows 2 Dec 2027 / 2 Aug 2028; deleted provisions shown as "__________"; recitals from original 32024R1689 |
| DORA | 32022R2554 | none — no consolidated version; original OJ L 333, 27.12.2022 is current | HTML, PDF, OJ | ✅ | ✅ | articles + recitals both from the original |
| AI Omnibus | 32026R1744 | none — original act, OJ L 2026/1744, 24.7.2026 | HTML, PDF, OJ | ✅ | ✅ | amendment instructions ("erhält folgende Fassung"); context for "what changed / why", not the main source |

## Markup of GDPR Art. 33(1)
(HTML: … / XML: …)

## Recommendation
Step 6 — HTML markup (GDPR Art. 33, consolidated EN)
- Article: `div.eli-subdivision#art_33` (inserted articles: `#art_4a`, `#art_75b`)
- Heading: `p.title-article-norm`; title: `div.eli-title#art_33.tit_1 > p.stitle-article-norm`
- Paragraphs: inside `div.norm` (paragraph ids: <fill in>)
→ Decision: parse HTML with BeautifulSoup using ELI ids; article number from the element id, not from text.

## Download channel (tested 2026-11-04)
- EUR-Lex website (`eur-lex.europa.eu/legal-content/...`) answers scripts with **HTTP 202, 0 bytes**: a bot challenge. Not worked around.
- **Cellar** (Publications Office machine interface) works: `http://publications.europa.eu/resource/celex/{CELEX}` with
  `Accept: application/xhtml+xml` and `Accept-Language: eng|deu` → 200, XHTML, same ELI markup.
  GDPR original: 173 `id="rct_N"` recitals; consolidated: `div#art_N` per article.
- Manifest keeps two URLs: `source_url` (EUR-Lex, for human citations in answers) and `fetch_url` + `fetch_lang` (Cellar, for the extractor).