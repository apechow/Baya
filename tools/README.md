# Baya Tools

`tools/` contains deterministic helpers used in Baya_Code that generated benchmark scripts can import without accessing agent internals. The current fuzzy matching implementation uses only the Python standard library and performs no network requests, file I/O, or configuration loading.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package marker. Import helpers from `tools.fuzzy_match`. |
| `fuzzy_match.py` | Text normalization, similarity scoring, candidate ranking, and best-match selection. |
| `tests/__init__.py` | Test package marker. |
| `tests/test_fuzzy_match.py` | Tests matching precedence, normalization, field selection, thresholds, ambiguity, stable ordering, and edge cases. |
| `README.md` | Usage and behavior reference. |

## Usage

Run from the repository root, or ensure the repository is on Python's import path:

```python
from tools.fuzzy_match import best_match, matches, rank_matches

products = [
    {"name": "Orchid Fertilizer", "sku": "ab11"},
    {"name": "Hawaiian Bamboo Orchid Roots", "sku": "zc50"},
]

found = best_match("zc50", products, key=("name", "sku"))
assert found is not None
assert found.item is products[1]
assert found.method == "exact"
assert found.matched_on == "sku"

assert matches("deep learning", "deeplearning")
ranked = rank_matches("orchid", products, key="name", limit=2)
assert len(ranked) == 2
```

`key` can be omitted for plain string candidates, a field/attribute name, several field names, or a callable. A callable can return text, a mapping of field names to text, or a list/tuple of texts. Missing fields and `None` are tolerated; non-string values are converted to strings.