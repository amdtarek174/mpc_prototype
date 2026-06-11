"""
Light-weight formula evaluator for the APC dashboard.

The formulas on the `Formulas <FC>` sheet are written in informal English
("singles s/m volume/singles s/m rate", "(each receive volume*
(1-Non sort each receive%)/Each receive rate)").  This module:

* Tokenises a formula into a list of *variables* (one per distinct phrase
  the user must provide a value for).
* Substitutes user-provided numbers back into the formula and evaluates
  it with a sand-boxed ``eval``.

Why not a full grammar?  Because the formulas mix natural language with
arithmetic.  Phrases like ``s/m`` use ``/`` as part of the name, ``%`` is
sometimes an operator and sometimes a suffix, and a few formulas contain
words like ``sum``, ``buffer`` or ``or else`` that aren't real operators.
We protect the well-known cases and surface anything we can't evaluate as
a friendly error so the variable list still works as a manual checklist.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Slash-in-name protection
# ---------------------------------------------------------------------------
#
# A handful of canonical input phrases legitimately contain ``/`` (``s/m``,
# ``m/s``, ``MM/s``, ``M/s``).  Before tokenising the formula on math
# operators we replace these with placeholders so the splitter doesn't
# treat them as division.  After splitting we put them back so the user
# sees the original phrasing.

_PROTECTED: List[Tuple[str, str]] = [
    (r"\bMM/s\b",  "<MMs>"),
    (r"\bMM/S\b",  "<MMs>"),
    (r"\bs/m\b",   "<SM>"),
    (r"\bm/s\b",   "<MS>"),
    (r"\bS/M\b",   "<SM>"),
    (r"\bM/S\b",   "<MS>"),
    (r"\bM/s\b",   "<MlowS>"),
]

_RESTORE = {
    "<MMs>":   "MM/s",
    "<SM>":    "s/m",
    "<MS>":    "m/s",
    "<MlowS>": "M/s",
}

# Words that look like variables in some formulas but are really English
# stop-words.  We strip them out so the calculator doesn't ask the user
# for "buffer" or "sum".
_NOISE_WORDS = {
    "buffer", "sum", "total", "of", "and", "with", "or", "else", "if",
    "from", "by", "on", "no", "yes", "the", "a", "an",
}

# A stripped-down split pattern that splits on math operators while keeping
# them around so we can recombine.
_SPLIT_PATTERN = re.compile(r"([+\-*/(),])")


def _protect(text: str) -> str:
    for pat, rep in _PROTECTED:
        text = re.sub(pat, rep, text)
    return text


def _restore(text: str) -> str:
    for ph, original in _RESTORE.items():
        text = text.replace(ph, original)
    return text


def _normalize(text: str) -> str:
    """Collapse whitespace; trim leading/trailing space."""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_variables(formula: str) -> List[str]:
    """Return the ordered list of variable names found in *formula*.

    Variables are everything between math operators that isn't a pure
    number or a stop-word.
    """

    if not formula:
        return []

    # For conditional formulas ("If 100% SPPB-> ML1PPB vol/ML1PPB rate or
    # else 0") the right-hand side after "->" is what holds the inputs.
    work = formula
    if "->" in work:
        work = work.split("->", 1)[1]
    # Drop "or else <number>" tail; the user can apply the fall-back
    # manually, we don't want to treat "or else 0" as variables.
    work = re.sub(r"\bor\s+else\b.*", "", work, flags=re.IGNORECASE)
    work = re.sub(r"^\s*If\b.*?(?=\b\w)", "", work, flags=re.IGNORECASE)

    protected = _protect(work)
    parts = _SPLIT_PATTERN.split(protected)

    variables: List[str] = []
    seen: set = set()

    for p in parts:
        token = p.strip()
        if not token or token in "+-*/(),":
            continue
        # Pure numeric (optionally with trailing percent)
        if re.fullmatch(r"\d+(?:\.\d+)?\s*%?", token):
            continue
        var = _restore(_normalize(token))

        # If the var is just a noise word, skip it
        if var.lower() in _NOISE_WORDS:
            continue

        # Drop trailing punctuation that aren't operators (rare)
        var = var.rstrip(":;.")
        var = var.lstrip("> ")  # leftover from "->"

        if not var or var in seen:
            continue

        seen.add(var)
        variables.append(var)

    return variables


def evaluate(
    formula: str,
    values: Dict[str, float],
    default_unknown: float = 0.0,
) -> Tuple[Optional[float], str]:
    """Substitute *values* into *formula* and evaluate.

    Returns ``(result, message)``.  ``result`` is ``None`` if the formula
    can't be evaluated (typically because it contains free text such as
    "or else", "sum(...)" with conceptual operands, or something we
    couldn't substitute).  ``message`` always describes the outcome.
    """

    if not formula:
        return None, "No formula to evaluate."

    # Conditional / English-logic formulas can't be evaluated mechanically.
    if re.search(r"\b(if|or else)\b", formula, flags=re.IGNORECASE) or "->" in formula:
        return None, (
            "This formula contains conditional logic (if / or else / ->) and "
            "can't be evaluated automatically. Use the inputs as a checklist "
            "and apply the conditional manually."
        )

    expr = _protect(formula)

    # Substitute variables in *longest-first* order so "single large pack
    # rate" wins over "single large".
    for var in sorted(values, key=lambda k: -len(k)):
        protected_var = _protect(var)
        # Use plain (re.escape) so we match the phrase exactly; case
        # insensitive because the formulas mix capitalisation.
        v = values.get(var, default_unknown)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = default_unknown
        expr = re.sub(re.escape(protected_var), f"({v})", expr, flags=re.IGNORECASE)

    # Restore any leftover protected names so the failure message reads
    # naturally if eval blows up.
    expr_restored = _restore(expr)

    # Convert "<NUMBER>%" into a fractional multiplier.
    expr2 = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr_restored)

    # Strip noise words that survive substitution (e.g. "*10% buffer").
    for word in sorted(_NOISE_WORDS, key=lambda w: -len(w)):
        expr2 = re.sub(rf"\b{word}\b", "", expr2, flags=re.IGNORECASE)

    # Tidy whitespace and trailing operators.
    expr2 = _normalize(expr2)

    # Auto-balance parentheses (a couple of source formulas have mismatched
    # parens — we fix them silently and flag in the message).
    paren_fix_msg = ""
    open_count = expr2.count("(")
    close_count = expr2.count(")")
    if open_count > close_count:
        expr2 = expr2 + (")" * (open_count - close_count))
        paren_fix_msg = f" (auto-added {open_count - close_count} closing paren(s) to balance)"
    elif close_count > open_count:
        expr2 = ("(" * (close_count - open_count)) + expr2
        paren_fix_msg = f" (auto-added {close_count - open_count} opening paren(s) to balance)"

    # If after substitution there are still alpha tokens left, the formula
    # isn't fully numeric — bail out early with a helpful message.
    leftover = re.findall(r"[A-Za-z][A-Za-z%/\- ]*", expr2)
    leftover = [t.strip() for t in leftover if t.strip()]
    if leftover:
        return None, (
            "Couldn't fully evaluate this formula — these phrases remain "
            "after substitution: " + ", ".join(sorted(set(leftover)))
        )

    # Try the eval in a sand-box.  Only arithmetic operators are needed.
    try:
        result = eval(expr2, {"__builtins__": {}}, {})  # noqa: S307
    except ZeroDivisionError:
        return None, "Division by zero in the formula."
    except Exception as exc:  # pragma: no cover - surfaced verbatim
        return None, f"Could not evaluate expression: {exc}"

    try:
        return float(result), "OK" + paren_fix_msg
    except (TypeError, ValueError):
        return None, f"Result was not numeric: {result!r}"


def is_evaluable(formula: str) -> bool:
    """Best-effort check: can the formula be evaluated with all-1 inputs?"""

    vars_ = extract_variables(formula)
    if not vars_:
        return False
    res, _ = evaluate(formula, {v: 1.0 for v in vars_})
    return res is not None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    samples = [
        "(singles s/m volume/singles s/m rate)+(HOV volume/HOV rate)",
        "SLAP volume/SLAP rate",
        "TP volume/TP rate",
        "(Single large volume/single large pack rate)",
        "ML volume/ML sort batch rate",
        "(each receive volume*(1-Non sort each receive%)/Each receive rate)+((case receive volume/(case receive rate*units per case))",
        "(Pallet receive volume/Unit per pallet from FCLM)*6 + ((Each receive volume+Case receive volume+LP receive volume)/Unit per pallet from dockmaster with 20% buffer)*6",
        "If 100% SPPB-> ML1PPB vol/ML1PPB rate or else 0",
        "(TSI volume/Unit per TSI pallet)*6",
    ]
    for s in samples:
        vars_ = extract_variables(s)
        # Provide example values 100 for "volumes" and 50 for "rates"
        vals = {v: (100.0 if "volume" in v.lower() else 50.0 if "rate" in v.lower() else 1.0)
                for v in vars_}
        result, msg = evaluate(s, vals)
        print(f"\nFORMULA : {s}")
        print(f"  vars  : {vars_}")
        print(f"  values: {vals}")
        print(f"  result: {result}  ({msg})")
