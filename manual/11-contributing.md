# Contributing

The most useful thing you can do is test a console we haven't seen. The second most useful thing is telling us exactly how it went wrong.

## Send a hardware report

Run [DiagnostiX](06-diagnostix.md), or boot a [kernel](03-kernels.md) your own way, and fill in the [report form](https://github.com/meerzulee/linux-psx/issues/new?template=report.yml). [Results and Reports](07-results-and-reports.md) lists what to include and what to keep private.

Reports from consoles other than a Baikal Slim are especially welcome. So are negative results. If you'd like to go further and get your console working, see [Help Port Your Console](09-help-port-your-console.md).

## Send a patch

Linux psX builds from a pinned upstream Linux, a resolved configuration and an ordered list of patches. Generated source trees are disposable, so work in a separate checkout and save your change as a patch.

A pull request should:

- describe the concrete failure and the resulting behavior;
- give the source of each change, with its original author and license;
- say which checks you actually ran, and what hardware limits remain.

Preserve existing notices and patch authorship, and include a `Signed-off-by` when you can certify the contribution. Run this before you open it:

```sh
make check
```

The full rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Adding a console or a Linux version

Create a new target with a pinned base commit, its own configuration and a reviewed patch order, instead of changing an existing one. Record what's imported, what's only for comparison and what's still untested.

Whether PS4 and PS5 can share a kernel is a [separate question](../docs/UNIVERSAL-KERNEL.md).

## The rules for hardware testing

Hardware results only count if they can be trusted later:

- One experiment answers one question, with a stated pass condition, a timeout and a way back.
- Change one variable at a time.
- Don't repeat a failure without new evidence or a changed variable. After three identical failures, stop and go down a layer.
- A successful boot once is evidence. Calling something supported takes repeated runs on the same build.
- A build, a clean log or an agreement between reviewers isn't a hardware result.

The full process, including continuous UART capture, is in [Hardware testing](../docs/HARDWARE-TESTING.md). The project's own experiments are recorded in the [session ledger](../diagnostics/SESSIONS.md).

## Thanks

Linux psX is built on years of community work. Thanks to everyone in the [credits](../CREDITS.md), and to [suds-mcduff](https://github.com/suds-mcduff) for the PS4 HDMI underscan and Baikal power-off contributions and for careful hardware reports.

<!-- manual-navigation -->

---

[← Previous](10-building-from-source.md) · [Contents](README.md) · [Next →](12-legal-notice.md)
