# Third-party notices

The LICENSE file of this repository (MIT) covers the TokenBench code only, and
LICENSE-DATA.md (CC BY 4.0) covers `results/` and the TokenBench-written task text
in `tasks/short_tasks.jsonl` and `tasks/custom_tasks.jsonl`. The material below comes
from other projects and keeps the licence of its source.

Everything under `tasks/swe_bench_pro_chains/` (`chain.json`, `validation.json`,
`grade.py` and `container/`) derives from SWE-bench Pro and stays under its upstream
terms; the files in the first section below are the ones whose upstream licence is
stated in this repository.

## SWE-bench Pro harness files

The following files under `tasks/swe_bench_pro_chains/*/container/tasks/NN/` are
copies of files from the SWE-bench Pro harness, one set per task:

* `parser.py`
* `args.txt`
* `run_script.sh`

Upstream project: https://github.com/scaleapi/SWE-bench_Pro-os (MIT License).
Dataset: https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro

The files themselves carry no copyright header, so the upstream copyright holder
line is given here as:

```
Copyright (c) the SWE-bench Pro authors
```

The MIT License text that applies to these files:

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

For the exact upstream copyright notice, see the upstream repository.

## SWE-bench Pro task content

Each `chain.json` lists instance ids, base commits, docker images and test names
(fail-to-pass and pass-to-pass) that identify rows of the SWE-bench Pro dataset.
These names and identifiers originate from the upstream repositories below, which
the SWE-bench Pro tasks are drawn from. Their issue text and test patches are not
redistributed here (see `tasks/README.md`). `validation.json` and `grade.py` in each
chain directory were produced for the same chains and are covered by the same
statement.

The licence of each upstream repository is set by that repository, and several
upstream projects use GPL or AGPL licences; see each repository. This repository does
not state or assert them.

| Chain(s) | Upstream repository | Licence |
|---|---|---|
| `ansible_B`, `ansible_C`, `ansible_D` | https://github.com/ansible/ansible | set by the upstream repository |
| `flipt_B` | https://github.com/flipt-io/flipt | set by the upstream repository |
| `navidrome_A` | https://github.com/navidrome/navidrome | set by the upstream repository |
| `nodebb_A` | https://github.com/NodeBB/NodeBB | set by the upstream repository |
| `openlibrary_A`, `openlibrary_B` | https://github.com/internetarchive/openlibrary | set by the upstream repository |
| `qutebrowser_A`, `qutebrowser_B` | https://github.com/qutebrowser/qutebrowser | set by the upstream repository |
| `teleport_A` | https://github.com/gravitational/teleport | set by the upstream repository |
| `vuls_A` | https://github.com/future-architect/vuls | set by the upstream repository |
| `webclients_A` | https://github.com/protonmail/webclients | set by the upstream repository |

## Open-source repositories used by the short and custom tasks

Two of the eight repositories behind the short and custom tasks are open-source
projects. They are not included in this repository; they are listed because the
tasks refer to them (see `tasks/README.md`).

| Project | Commit | Licence |
|---|---|---|
| [SharpTS](https://github.com/nickna/SharpTS) | `33f266f97fcdf2f37f438f4eedaed3b4e7ce05d7` | MIT |
| [mvm](https://github.com/mvm-sh/mvm) | `f0d101dad05ec263ed1f195bee51ff2f4a7a7925` | BSD-3-Clause |

## Tools measured

The tools that were measured (caveman, graphify, ponytail, rtk) are third-party
projects and are not included in this repository. Each is distributed under its own
licence; see the upstream repository of each tool.
