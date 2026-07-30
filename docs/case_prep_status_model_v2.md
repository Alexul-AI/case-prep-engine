# Case-Prep Evidence Status Model v2

This model separates three questions that must not be collapsed:

1. Can we read the document?
2. Does the document support the claim?
3. May the claim be generated as a factual statement?

## 1. Text Quality Status

Use this for the document/text layer.

- `file_found`: the file exists, but no content quality judgment yet.
- `needs_ocr`: the file exists, but extraction is empty or unusable.
- `text_extracted_unverified`: extraction returned text, but no sanity check has been done.
- `text_qa_failed`: text exists but is unusable, for example reversed Hebrew, broken word order, or severe OCR corruption.
- `text_qa_passed`: the text is readable enough for analysis.

Important:

`text_qa_passed` does not mean the claim is supported. It only means the document can be analyzed.

## 2. Claim Support Status

Use this for the evidence/claim layer.

- `metadata_only`: only the file title/folder/metadata appears to support the claim.
- `provisional`: plausible from context, but not verified against source text.
- `not_checked`: readable source exists, but this claim has not been checked inside it yet.
- `supported_by_paraphrase`: the source supports the claim by synthesis across text, but there is no single clean quote.
- `supported_by_quote`: the source contains a concrete extract usable as direct support.
- `checked_not_supported`: the source was checked and does not support the claim.
- `contradicted`: the source was checked and directly contradicts the claim.

Important:

`checked_not_supported` is not a weak version of `needs_ocr`. It is a valuable negative finding.

## 3. Output Gate

Use this for generation.

- `blocked`: the claim must not be written as fact.
- `allowed_as_unverified`: may appear only as "needs checking" / "טרם אומת".
- `allowed_as_synthesis`: may appear as a factual synthesis, with source-note that it is not a direct quote.
- `allowed_as_quote`: may appear as a fact with exact quote/reference.
- `allowed_as_negative_finding`: may appear as "checked, not found" or "the document does not support this claim".
- `allowed_as_contradiction`: may appear as a contradiction/risk flag.

## Output Rules

Claims with these statuses are blocked from factual generation:

- `metadata_only`
- `needs_ocr`
- `text_extracted_unverified`
- `text_qa_failed`
- `provisional`
- `not_checked`

They may only be generated as:

- "צריך לבדוק"
- "טרם אומת"
- "קיים מסמך בשם X, אך טרם חולץ ממנו טקסט תקין"
- "לא ניתן להסתמך על כך בשלב זה"

Claims with `supported_by_paraphrase` may be used in briefs, but must be marked as synthesis, not as a direct quote.

Claims with `supported_by_quote` may be used as direct evidence.

Claims with `checked_not_supported` or `contradicted` should be surfaced explicitly. These are often the most valuable findings.

## Design Note

Do not let a readable document promote a claim automatically.

The pipeline must pass through:

file found -> text QA -> claim check -> generation permission

Skipping any step creates false confidence.
