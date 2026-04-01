# Findings

## Requirements

- Build the current `agent-factory` workspace into a distributable CLI artifact.
- Verify the built executable before publishing.
- Commit and push the deployment payload to the deployment repository.

## Research Findings

- `build_exe.py` is the explicit build entrypoint and produces `dist/af/af.exe` plus `dist/af-<version>.zip`.
- `version.py` currently declares `1.2.8`.
- `install-af.ps1` in this source worktree downloads from `https://github.com/hoonkim1092-web/af-fsa/raw/af-fsa_v1.2.8/dist/af-1.2.8.zip`.
- `git ls-remote https://github.com/hoonkim1092-web/af-fsa.git` confirmed the external deployment repo exists and has branch `af-fsa_v1.2.8`.
- The local worktree has unrelated dirty files, mostly sync/context outputs; the deployment publish step should target the built artifact repo, not blindly commit local source noise.

## Technical Decisions

| Decision | Rationale |
| --- | --- |
| Use external repo `af-fsa` branch `af-fsa_v1.2.8` as deployment target | Matches installer download URL and existing deployment branch scheme |
| Build from current worktree instead of older deployment repo source | User explicitly requested building the current `agent-factory` state |
| Verify using `dist/af/af.exe --help` after build | Confirms the packaged executable actually starts |

## Issues Encountered

| Issue | Resolution |
| --- | --- |
| Local sandboxed shell was unusable | Moved required shell work to escalated commands |
| Prior planning files were from a different task | Replaced them with deployment-specific notes for this session |

## Resources

- `build_exe.py`
- `install-af.ps1`
- `version.py`
- `git ls-remote https://github.com/hoonkim1092-web/af-fsa.git`

## Visual/Browser Findings

- No browser-based findings for this task.
