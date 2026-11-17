"""Check that an answer only states sites and figures the evidence contains.

Evidence is the question, earlier turns of the conversation, the tool inputs and the
tool results. An answer number is supported when some evidence number rounds to it
at the answer's precision: 1,086 acres is supported by 1086.4, and "under 1 mile" by
an input of 0.99. Whole numbers up to 10 are always allowed, since answers count
things ("the 3 largest", "2 sites").

This catches invented figures and site IDs. It cannot catch a supported number put
in the wrong place, such as one site's acreage given for another.
"""
import re

# Fixture IDs such as W-001, and parcel numbers. Road names such as I-76 or US-85 are not IDs.
SITE_ID = re.compile(r"\b(?!(?:I|US|CO|SH|SR|CR)-)[A-Z]{1,3}-\d{2,}\b|\b\d{9,}\b")
NUMBER = re.compile(r"(?<![\w.\-])-?\d[\d,]*(?:\.\d+)?")
SMALL = 10


def numbers(text):
    """Numbers written in text, as (token as written, value, decimal places)."""
    found = []
    for match in NUMBER.finditer(text):
        token = match.group().rstrip(",")
        digits = token.replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        places = len(digits.split(".")[1]) if "." in digits else 0
        found.append((token, value, places))
    return found


def supported(value, places, evidence):
    if places == 0 and value.is_integer() and 0 <= value <= SMALL:
        return True
    return any(round(e, places) == value or abs(e - value) < 1e-9 for e in evidence)


def unsupported(answer, evidence_texts, forbid_numbers=()):
    """Site IDs and numbers in the answer that no evidence supports, in the order written.

    forbid_numbers lists values that fail even when the evidence contains them, for
    claims planted in a question.
    """
    evidence = " \n".join(evidence_texts)
    known_ids = set(SITE_ID.findall(evidence))
    problems = [site for site in SITE_ID.findall(answer) if site not in known_ids]
    text = SITE_ID.sub(" ", answer)
    evidence_numbers = [value for _, value, _ in numbers(SITE_ID.sub(" ", evidence))]
    for token, value, places in numbers(text):
        if value in forbid_numbers or not supported(value, places, evidence_numbers):
            problems.append(token)
    return list(dict.fromkeys(problems))
