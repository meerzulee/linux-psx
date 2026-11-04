# Optional Steam and game compatibility view

Status: the diagnostics menu includes an optional **ProtonDB** browser action
as of 2026-11-05. It opens the fixed public website in Firefox with a fresh
private profile under the unprivileged diagnostic user. It is manual browsing:
it does not start the browser test batch, create a game-test report, install
Steam or Proton, or establish PS4 game support. Close the browser to return to
diagnostics. Network access is required to load the website.

Steam installation and a local game-compatibility catalogue remain proposed
optional additions outside the 1 GiB core budget.

The useful existing sources are [ProtonDB](https://www.protondb.com/), which
collects community Linux reports, and Valve's
[Steam Deck compatibility program](https://www.steamdeck.com/en/verified).
Deck ratings describe Deck hardware and its software stack. They are context
for a PS4 estimate, not a PS4 test result. Unknown or Deck Unsupported must
never be translated automatically into a PS4 hardware failure.

The proposed view keeps three independent facts: upstream Linux compatibility,
the local PS4 estimate with its reason, and measured local performance. Use:

| PS4 estimate | Required basis |
| --- | --- |
| Tested on PS4 | A recorded game/version, console, kernel/Mesa/Proton identity, settings, finite run and observed result |
| Likely | Relevant external reports plus compatible measured requirements; explicitly an estimate |
| Blocked | A verified mandatory requirement or known blocker that applies to this exact setup |
| Unknown | Missing requirements, unqualified APIs, conflicting reports or no local evidence |

Show source links, report dates and the reason. Do not invent an FPS estimate
from a ProtonDB rating or compare a PS4 directly with Deck. A game can be
compatible and still perform poorly. Search/metadata browsing can be useful
without installing Steam or signing into an account. No credentials or Steam
library import are needed for the initial design.

Read-only inspection of the current console's `/proc/cpuinfo` reported SSE2,
SSE4.1, SSE4.2 and AVX, with no AVX2 or FMA. A game explicitly requiring AVX2
without a fallback can be identified as blocked on this observed CPU profile.
A generic minimum CPU name or a game's age is not proof of that requirement.
Record and refresh the actual CPU flags before applying such a rule on another
machine. These flags were an inventory observation, not a game execution test.

The current console has short hardware OpenGL/WebGL evidence. Vulkan game
support, a matching 32-bit graphics stack, Steam runtime behavior and actual
games remain unqualified. Qualify those dependencies before launching a game
that needs them. Valve documents Proton's Wine-based compatibility layer and
its [runtime options](https://github.com/ValveSoftware/Proton#runtime-config-options),
including the OpenGL wined3d alternative to DXVK for older Direct3D APIs.
An available fallback is not a game-performance result.

Steam, Proton prefixes, updates and game saves need a suitable persistent,
writable location; the current diagnostic root is read-only with bounded RAM
writes. Select and review the optional storage layout separately. No partition,
format, internal-disk write or Steam account operation is part of this design.
Any catalogue integration must first verify its data interface, reuse terms,
freshness and bounded caching. Do not depend silently on an undocumented API.
