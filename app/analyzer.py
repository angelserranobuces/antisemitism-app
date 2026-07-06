"""
Claude-powered antisemitism framing risk analyzer.
Implements the IHRA-informed methodology with 9 weighted categories.

Two analysis modes:
  1. Text mode   — extracted text sent as plain content (fast, for searchable PDFs)
  2. PDF mode    — raw PDF bytes sent as a Claude document (handles scanned/image PDFs)
"""

import os
import json
import base64
import logging
from typing import Dict, Any, List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

MAX_PDF_BYTES   = 32 * 1024 * 1024  # 32 MB — Claude API hard limit per document
PAGES_PER_CHUNK = 4                  # pages per vision API call (conservative)
IMAGE_DPI       = 96                 # 96 DPI — ~800px wide, readable but not huge
MAX_PAGES_TOTAL = 48                 # process first 48 pages max

# ── Methodology constants ──────────────────────────────────────────────────────

CATEGORIES = {
    "collective_blame":                {"label": "Collective Blame",                    "weight": 0.20},
    "conspiracy_tropes":               {"label": "Conspiracy Tropes",                   "weight": 0.20},
    "holocaust_inversion":             {"label": "Holocaust Inversion",                 "weight": 0.15},
    "demonisation":                    {"label": "Demonisation",                        "weight": 0.10},
    "denial_jewish_self_determination":{"label": "Denial of Jewish Self-Determination", "weight": 0.10},
    "double_standards":                {"label": "Double Standards",                    "weight": 0.10},
    "dehumanisation":                  {"label": "Dehumanisation",                      "weight": 0.05},
    "asymmetric_empathy":              {"label": "Asymmetric Empathy",                  "weight": 0.05},
    "contextual_omission":             {"label": "Contextual Omission",                "weight": 0.05},
}

# ── Prompts ────────────────────────────────────────────────────────────────────

METHODOLOGY_PROMPT = """
You are an expert media analyst applying the IHRA-informed Replicable Methodology for Evaluating Antisemitic Framing Risk in Media.

METHODOLOGY OVERVIEW:
Evaluate framing-risk INDICATORS — not proven intent. Focus on cumulative editorial patterns. Every score must be evidence-based and auditable.

IMPORTANT CALIBRATION (anchoring values from 19 May 2026 editions):
- El País scored 2.3/5 overall: main triggers were UN genocide accusation language ("La ONU acusa a Israel de un posible delito de genocidio"), flotilla interception framing, high asymmetric empathy (3.0) and contextual omission (3.0), demonisation (2.5), double standards (2.5). No collective blame, conspiracy tropes, Holocaust inversion, or dehumanisation.
- El Mundo scored 1.6/5: "expansionismo israelí", "plan israelí", geopolitical framing of Israel/Iran/Gulf; asymmetric empathy (2.0), contextual omission (2.0), demonisation (1.5), double standards (1.5). Mitigating factor: article on antisemitic incidents in Madrid metro.
- La Razón scored 1.1/5: security/geopolitical framing across multiple actors; asymmetric empathy (1.5), contextual omission (1.5), demonisation (1.0), double standards (1.0).
- ABC scored 0.8/5: sparse Israel/Gaza coverage, dominant domestic politics; asymmetric empathy (1.0), contextual omission (1.0), demonisation (0.5), double standards (0.5).

NINE CATEGORIES AND DEFINITIONS:
1. collective_blame (weight 20%) — Blaming Jews collectively for Israel or Israeli government conduct.
2. conspiracy_tropes (weight 20%) — Hidden Jewish/Zionist control, manipulation, finance, or media power.
3. holocaust_inversion (weight 15%) — Equating Israel/Jews with Nazis or inverting victimhood using Holocaust imagery.
4. demonisation (weight 10%) — Portraying Israel as uniquely evil, bloodthirsty, monstrous without context.
5. denial_jewish_self_determination (weight 10%) — Rejecting legitimacy of Jewish national self-determination as such.
6. double_standards (weight 10%) — Applying standards to Israel not applied to comparable states/conflicts.
7. dehumanisation (weight 5%) — Animalising or subhuman language for Israelis/Jews. Rare but severe.
8. asymmetric_empathy (weight 5%) — Systematically erasing Israeli/Jewish civilian suffering or security concerns.
9. contextual_omission (weight 5%) — Omitting Hamas, hostages, attacks, rocket fire, or security rationale.

SCORING RUBRIC (0–5 per category):
0 = No meaningful indicator detected
1 = Very low: isolated or ambiguous single occurrence
2 = Low-moderate: limited pattern, some contextual balance present
3 = Moderate: clear pattern, partially unbalanced framing
4 = High: strong repeated pattern, significantly unbalanced
5 = Very high: explicit, repeated, severe indicators

OVERALL SCORE FORMULA:
FRS = (CB×0.20) + (CT×0.20) + (HI×0.15) + (D×0.10) + (DSD×0.10) + (DS×0.10) + (DH×0.05) + (AE×0.05) + (CO×0.05)

CALIBRATION NOTE: When collective_blame AND conspiracy_tropes are both 0, the effective score range is compressed. Multiply the raw FRS by ~2.5 to align with the calibrated anchoring values above.

EVIDENCE STANDARD:
- High confidence: explicit and repeated evidence
- Medium confidence: strong framing pattern, alternative interpretations possible
- Low confidence: isolated or ambiguous evidence

LEGITIMATE CRITICISM SAFEGUARD:
Criticism of Israeli government policy, military conduct, or specific officials is NOT antisemitic per se. Scores are moderated when non-antisemitic interpretations are plausible.
"""

ANALYSIS_INSTRUCTION_TEXT = """
Analyze the following newspaper text for antisemitic framing risk using the methodology above.

NEWSPAPER: {newspaper}
DATE: {date}
FILENAME: {filename}

EXTRACTED TEXT:
{text}

Respond with ONLY valid JSON (no markdown fences, no text outside the JSON):
{{
  "category_scores": {{
    "collective_blame": {{"score": <0-5 or "Not Determined">, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "conspiracy_tropes": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "holocaust_inversion": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "demonisation": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "denial_jewish_self_determination": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "double_standards": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "dehumanisation": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "asymmetric_empathy": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "contextual_omission": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}}
  }},
  "overall_rating": <0-5 float>,
  "risk_level": "<No Risk|Very Low|Low|Low-Moderate|Moderate|High|Very High>",
  "confidence_overall": "<High|Medium|Low>",
  "main_drivers": "<2-3 sentence summary>",
  "mitigating_factors": "<factors that reduce score>",
  "evidence_items": [
    {{
      "category": "<category_key>",
      "trigger_phrase": "<exact phrase>",
      "article_title": "<article or section>",
      "page_reference": "<page number or N/A>",
      "reason": "<why this contributes to risk>",
      "impact": "<High|Medium|Low>"
    }}
  ],
  "methodology_version": "1.0",
  "framework_references": ["IHRA Working Definition of Antisemitism (2016)", "Entman (1993) Framing Theory", "van Dijk (1991) Racism and the Press"]
}}

If the newspaper has minimal Israel/Jewish/Middle East content, assign 0 to most categories and a low overall rating (0.1–0.5).
"""

ANALYSIS_INSTRUCTION_PDF = """
Read the attached newspaper PDF carefully — it is a Spanish newspaper edition.
Analyze it for antisemitic framing risk using the methodology above.

NEWSPAPER: {newspaper}
DATE: {date}
FILENAME: {filename}

Focus on: headlines, sub-headlines, article bodies, captions, opinion columns, pull quotes, and editorial content.
Pay special attention to any content related to Israel, Gaza, Iran, Palestine, Jewish people, antisemitism, or the Middle East conflict.

Respond with ONLY valid JSON (no markdown fences, no text outside the JSON):
{{
  "category_scores": {{
    "collective_blame": {{"score": <0-5 or "Not Determined">, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "conspiracy_tropes": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "holocaust_inversion": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "demonisation": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "denial_jewish_self_determination": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "double_standards": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "dehumanisation": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "asymmetric_empathy": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}},
    "contextual_omission": {{"score": <0-5>, "confidence": "<High|Medium|Low>", "evidence": "<brief evidence>", "triggers": []}}
  }},
  "overall_rating": <0-5 float>,
  "risk_level": "<No Risk|Very Low|Low|Low-Moderate|Moderate|High|Very High>",
  "confidence_overall": "<High|Medium|Low>",
  "main_drivers": "<2-3 sentence summary>",
  "mitigating_factors": "<factors that reduce score>",
  "evidence_items": [
    {{
      "category": "<category_key>",
      "trigger_phrase": "<exact phrase from the newspaper>",
      "article_title": "<article or section title>",
      "page_reference": "<page number if visible, else N/A>",
      "reason": "<why this contributes to risk>",
      "impact": "<High|Medium|Low>"
    }}
  ],
  "methodology_version": "1.0",
  "framework_references": ["IHRA Working Definition of Antisemitism (2016)", "Entman (1993) Framing Theory", "van Dijk (1991) Racism and the Press"]
}}

If the newspaper has minimal Israel/Jewish/Middle East content, assign 0 to most categories and a low overall rating (0.1–0.5).
"""


# ── Scoring formula ────────────────────────────────────────────────────────────

def compute_overall_rating(category_scores: Dict[str, Any]) -> float:
    """Fallback formula when Claude's overall_rating is missing or invalid."""
    raw = 0.0
    for key, meta in CATEGORIES.items():
        score_data = category_scores.get(key, {})
        score = score_data.get("score", 0) if isinstance(score_data, dict) else score_data
        if score in ("Not Determined", None, ""):
            score = 0
        try:
            raw += float(score) * meta["weight"]
        except (TypeError, ValueError):
            pass

    cb = category_scores.get("collective_blame", {})
    ct = category_scores.get("conspiracy_tropes", {})
    cb_score = cb.get("score", 0) if isinstance(cb, dict) else cb
    ct_score = ct.get("score", 0) if isinstance(ct, dict) else ct
    if cb_score in (0, "Not Determined", None) and ct_score in (0, "Not Determined", None):
        raw *= 2.56

    return round(min(raw, 5.0), 2)


def _finalise(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and finalise overall_rating."""
    claude_overall = analysis.get("overall_rating")
    try:
        val = float(claude_overall)
        if 0 <= val <= 5:
            analysis["overall_rating"] = round(val, 2)
            return analysis
    except (TypeError, ValueError):
        pass
    analysis["overall_rating"] = compute_overall_rating(analysis["category_scores"])
    return analysis


def _parse_response(response_text: str) -> Dict[str, Any]:
    """Strip markdown fences and parse JSON."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Find first line that starts with { and last line that ends with }
        start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), 1)
        end = next((i for i in range(len(lines)-1, -1, -1) if lines[i].strip().endswith("}")), len(lines)-1)
        text = "\n".join(lines[start:end+1])
    return json.loads(text)


# ── Mode 1: Text-based analysis ────────────────────────────────────────────────

def analyze_newspaper(
    text: str,
    newspaper: str,
    analysis_date: str,
    filename: str,
) -> Dict[str, Any]:
    """Analyze pre-extracted text via Claude API."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = ANALYSIS_INSTRUCTION_TEXT.format(
        newspaper=newspaper,
        date=analysis_date,
        filename=filename,
        text=text[:40000],
    )
    logger.info(f"[text mode] Sending {newspaper} ({analysis_date}) to Claude…")
    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=METHODOLOGY_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = _parse_response(msg.content[0].text)
        analysis = _finalise(analysis)
        logger.info(f"{newspaper}: overall={analysis['overall_rating']}")
        return analysis
    except Exception as e:
        logger.error(f"Text analysis failed for {newspaper}: {e}")
        return _error_analysis(newspaper, filename, str(e))


# ── Mode 2: PDF document analysis (for scanned / image PDFs) ──────────────────

def analyze_newspaper_pdf(
    pdf_bytes: bytes,
    newspaper: str,
    analysis_date: str,
    filename: str,
) -> Dict[str, Any]:
    """
    Send the newspaper PDF to Claude for analysis.

    Strategy (in order):
      1. Beta document API  — one call, whole PDF (requires beta header)
      2. Vision chunks      — renders pages to JPEG images, sends in batches
                              (no beta needed; works even on very large PDFs)
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = ANALYSIS_INSTRUCTION_PDF.format(
        newspaper=newspaper,
        date=analysis_date,
        filename=filename,
    )
    size_mb = len(pdf_bytes) / 1024 / 1024
    logger.info(f"[PDF mode] {newspaper} ({size_mb:.1f} MB)")

    # ── Attempt 1: Beta document API ──────────────────────────────────────────
    if len(pdf_bytes) <= MAX_PDF_BYTES:
        try:
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
            msg = client.beta.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                betas=["pdfs-2024-09-25"],
                system=METHODOLOGY_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            analysis = _parse_response(msg.content[0].text)
            analysis = _finalise(analysis)
            logger.info(f"{newspaper}: overall={analysis['overall_rating']} [beta-doc]")
            return analysis
        except Exception as e:
            logger.warning(f"Beta document API failed for {newspaper}: {e} — trying vision chunks")

    # ── Attempt 2: Vision chunks (PyMuPDF renders pages → JPEG → Claude vision) ─
    return _analyze_via_vision_chunks(
        pdf_bytes=pdf_bytes,
        newspaper=newspaper,
        analysis_date=analysis_date,
        filename=filename,
        client=client,
        prompt=prompt,
    )


def _pdf_to_jpeg_chunks(pdf_bytes: bytes) -> List[List[str]]:
    """
    Render PDF pages to JPEG using PyMuPDF's built-in encoder (no PIL needed).
    Returns list-of-lists: [[b64_page, ...], [...], ...]
    """
    import fitz  # pip3 install pymupdf

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = min(len(doc), MAX_PAGES_TOTAL)
    all_b64: List[str] = []
    mat = fitz.Matrix(IMAGE_DPI / 72, IMAGE_DPI / 72)

    for page_idx in range(total_pages):
        pix = doc[page_idx].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        # Use PyMuPDF's native JPEG export — no PIL dependency
        # tobytes API varies slightly by PyMuPDF version
        try:
            jpeg_bytes = pix.tobytes(output="jpeg", jpg_quality=70)
        except TypeError:
            jpeg_bytes = pix.tobytes("jpeg")
        size_kb = len(jpeg_bytes) / 1024
        logger.debug(f"  Page {page_idx+1}: {pix.width}×{pix.height}px → {size_kb:.0f} KB JPEG")
        all_b64.append(base64.standard_b64encode(jpeg_bytes).decode("utf-8"))

    doc.close()
    chunks = [all_b64[i:i + PAGES_PER_CHUNK]
              for i in range(0, len(all_b64), PAGES_PER_CHUNK)]
    logger.info(f"PDF → {total_pages} pages, {len(chunks)} chunk(s) of ≤{PAGES_PER_CHUNK}")
    return chunks


def _analyze_via_vision_chunks(
    pdf_bytes: bytes,
    newspaper: str,
    analysis_date: str,
    filename: str,
    client: anthropic.Anthropic,
    prompt: str,
) -> Dict[str, Any]:
    """
    Render PDF pages as JPEG images and send them to Claude vision.
    For PDFs with many pages, sends multiple chunks and merges results.
    """
    try:
        chunks = _pdf_to_jpeg_chunks(pdf_bytes)
    except ImportError:
        return _error_analysis(
            newspaper, filename,
            "PyMuPDF not installed. Run: pip3 install pymupdf"
        )
    except Exception as e:
        return _error_analysis(newspaper, filename, f"PDF render failed: {e}")

    if not chunks:
        return _error_analysis(newspaper, filename, "PDF produced no pages")

    chunk_analyses: List[Dict[str, Any]] = []
    last_error = "Unknown error"

    for chunk_idx, page_b64_list in enumerate(chunks):
        page_start = chunk_idx * PAGES_PER_CHUNK + 1
        page_end   = page_start + len(page_b64_list) - 1
        logger.info(f"{newspaper}: vision chunk {chunk_idx+1}/{len(chunks)} "
                    f"(pages {page_start}–{page_end}, {len(page_b64_list)} images)")

        content = []
        for b64 in page_b64_list:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })

        chunk_prompt = (
            f"These are pages {page_start}–{page_end} of {newspaper} ({analysis_date}).\n"
            + prompt
        )
        content.append({"type": "text", "text": chunk_prompt})

        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=METHODOLOGY_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            chunk_analysis = _parse_response(msg.content[0].text)
            chunk_analyses.append(chunk_analysis)
            logger.info(f"{newspaper} chunk {chunk_idx+1}: OK")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"{newspaper} chunk {chunk_idx+1} failed: {e}")
            continue

    if not chunk_analyses:
        return _error_analysis(
            newspaper, filename,
            f"Vision analysis failed on all chunks. Last error: {last_error}"
        )

    if len(chunk_analyses) == 1:
        result = _finalise(chunk_analyses[0])
        logger.info(f"{newspaper}: overall={result['overall_rating']} [vision-1-chunk]")
        return result

    # Merge multiple chunks: take max category score, combine evidence
    merged = _merge_chunk_analyses(chunk_analyses, newspaper, filename)
    logger.info(f"{newspaper}: overall={merged['overall_rating']} [vision-{len(chunk_analyses)}-chunks]")
    return merged


def _merge_chunk_analyses(
    analyses: List[Dict[str, Any]],
    newspaper: str,
    filename: str,
) -> Dict[str, Any]:
    """
    Merge results from multiple vision chunks.
    Takes the maximum score for each category (most conservative / highest risk seen).
    """
    merged_scores: Dict[str, Any] = {}
    all_evidence: List[Dict] = []

    for cat_key in CATEGORIES:
        best_score = 0
        best_confidence = "Low"
        best_evidence = ""
        best_triggers: List[str] = []

        for a in analyses:
            s = a.get("category_scores", {}).get(cat_key, {})
            if not isinstance(s, dict):
                continue
            raw = s.get("score", 0)
            if raw in ("Not Determined", None, ""):
                raw = 0
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = 0
            if val > best_score:
                best_score = val
                best_confidence = s.get("confidence", "Low")
                best_evidence = s.get("evidence", "")
                best_triggers = s.get("triggers", [])

        merged_scores[cat_key] = {
            "score": best_score,
            "confidence": best_confidence,
            "evidence": best_evidence,
            "triggers": best_triggers,
        }

    for a in analyses:
        all_evidence.extend(a.get("evidence_items", []))

    main_drivers = " ".join(
        a.get("main_drivers", "") for a in analyses if a.get("main_drivers")
    )[:500]

    merged = {
        "category_scores": merged_scores,
        "overall_rating": 0,
        "risk_level": "Unknown",
        "confidence_overall": "Medium",
        "main_drivers": main_drivers,
        "mitigating_factors": analyses[0].get("mitigating_factors", ""),
        "evidence_items": all_evidence,
        "methodology_version": "1.0",
        "framework_references": analyses[0].get("framework_references", []),
    }
    return _finalise(merged)


# ── Error fallback ─────────────────────────────────────────────────────────────

def _error_analysis(newspaper: str, filename: str, reason: str) -> Dict[str, Any]:
    nd = {k: {"score": "Not Determined", "confidence": "N/A",
               "evidence": f"Analysis failed: {reason}", "triggers": []}
          for k in CATEGORIES}
    return {
        "category_scores": nd,
        "overall_rating": 0.0,
        "risk_level": "Error",
        "confidence_overall": "N/A",
        "main_drivers": f"Analysis failed: {reason}",
        "mitigating_factors": "",
        "evidence_items": [],
        "methodology_version": "1.0",
        "framework_references": [],
        "error": reason,
    }
