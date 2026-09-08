# Fixed ladder scheduler example

This dependency-free example demonstrates Virtuoso's trusted external scheduler contract. It schedules successful attempts at 1, 3, 7, 14, then 30 days and keeps using 30 days. A failed attempt resets the next interval to 1 day. It is an illustrative policy, not a novel scheduling algorithm.

Copy this directory into the workspace and apply the required private modes:

```bash
mkdir -p workspace/modules
cp -R examples/modules/fixed-ladder workspace/modules/fixed-ladder
chmod 700 workspace/modules workspace/modules/fixed-ladder
chmod 600 workspace/modules/fixed-ladder/virtuoso.module.json
```

If `python3` does not identify the intended interpreter, replace the first manifest `command.argv` value with the explicit absolute path printed by:

```bash
python3 -c 'import sys; print(sys.executable)'
```

Select it in `workspace/virtuoso.json`:

```json
{
  "scheduler": {
    "algorithm": "module:fixed-ladder",
    "context": "atomic-recall",
    "configuration": {"intervals": [1, 3, 7, 14, 30]}
  }
}
```

Preserve the workspace's other top-level fields. Inspect the module before running it. Then authorize only the writing command that should execute it:

```bash
virtuoso --workspace workspace practice --item ITEM_ID --allow-trusted-scheduler
```

Use the same flag with `practice --administer` or `review record`. Queue, query, settings, switch, and doctor commands never execute the module.
