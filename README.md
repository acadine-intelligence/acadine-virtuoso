# Acadine Virtuoso

This repository is governed by **Acadine Build OS**. The source of truth is `product.json`; the supporting evidence lives in `docs/` and `.buildos/verification/`.

## Start here

```bash
BUILDOS_HOME="${BUILDOS_HOME:-$HOME/projects/acadine-build-os}"
python3 "$BUILDOS_HOME/scripts/buildos.py" check . --target value
```

Work through the gates in order: value → research → product → experience → architecture → implementation → verified → release → adoption. A red gate is a deliberate stop signal, not paperwork to bypass.

## Status language
Use `concept`, `validated-idea`, `researched`, `specified`, `designed`, `architected`, `implemented`, `verified`, `released`, or `adopted`. Never substitute “basically done,” “demo complete,” or “production-ready” without the corresponding evidence.
