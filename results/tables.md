### All tasks (71 units, 142 runs per tool, each set weighted equally)

Cost change vs baseline: negative = cheaper. Set columns show each set's own pooled cost change.

| Tool | Cost change vs baseline, sets weighted equally (95% range) | Short | Chains | Custom | Mean pass rate | Value index (baseline = 100) |
|---|---|---:|---:|---:|---:|---:|
| Baseline | reference | - | - | - | 81.1% | 100 |
| graphify 0.9.95 | -0.4% (-7.3% to +6.9%) | -0.7% | -11.4% | +10.9% | 74.6% | 99 |
| ponytail 5.3.2 | -18.3% (-21.8% to -14.3%) | -9.0% | -29.6% | -16.1% | 77.6% | 116 |
| caveman 1f951fb | +7.7% (-4.4% to +23.1%) | +7.1% | -9.3% | +25.5% | 80.1% | 93 |
| rtk 1.0.1 | +4.2% (-5.1% to +15.3%) | +0.5% | -0.3% | +12.3% | 74.9% | 94 |

### Short tasks (55 units, 110 runs per tool)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $0.141 | reference | 97/110 (88.2%) | 96.6 | 686 |
| graphify 0.9.95 | $0.140 | +0.7% (-4.4% to +5.9%) | 98/110 (89.1%) | 95.6 | 684 |
| ponytail 5.3.2 | $0.128 | +9.0% (+4.4% to +13.4%) | 98/110 (89.1%) | 96.1 | 750 |
| caveman 1f951fb | $0.151 | -7.1% (-12.0% to -2.9%) | 98/110 (89.1%) | 96.8 | 642 |
| rtk 1.0.1 | $0.141 | -0.5% (-4.6% to +3.9%) | 99/110 (90.0%) | 96.2 | 680 |

### SWE-bench Pro chains (13 units, 26 runs per tool)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $1.724 | reference | 23/26 (88.5%) | 88.5 | 51 |
| graphify 0.9.95 | $1.528 | +11.4% (+6.6% to +16.6%) | 22/26 (84.6%) | 84.6 | 55 |
| ponytail 5.3.2 | $1.213 | +29.6% (+21.2% to +37.1%) | 20/26 (76.9%) | 76.9 | 63 |
| caveman 1f951fb | $1.563 | +9.3% (-2.9% to +20.1%) | 22/26 (84.6%) | 84.6 | 54 |
| rtk 1.0.1 | $1.719 | +0.3% (-7.2% to +8.2%) | 22/26 (84.6%) | 84.6 | 49 |

### Custom long tasks (3 units, 6 runs per tool)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $11.802 | reference | 4/6 (66.7%) | 94.1 | 8 |
| graphify 0.9.95 | $13.088 | -10.9% (-34.2% to +8.2%) | 3/6 (50.0%) | 92.0 | 7 |
| ponytail 5.3.2 | $9.897 | +16.1% (+6.7% to +22.9%) | 4/6 (66.7%) | 91.3 | 9 |
| caveman 1f951fb | $14.807 | -25.5% (-71.8% to +3.7%) | 4/6 (66.7%) | 93.7 | 6 |
| rtk 1.0.1 | $13.251 | -12.3% (-49.1% to +15.8%) | 3/6 (50.0%) | 92.2 | 7 |
