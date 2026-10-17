# UART and diagnostic lessons

Continuous capture belongs to the separate `ps4-uart` tooling. Start a new
connection and bounded session on a new host; do not copy active PID, lock,
port-pointer or session state. Use
[HARDWARE-TESTING](../../../docs/HARDWARE-TESTING.md) for the current experiment
procedure. Historical shell capture, kexec, USB-swap and `/dev/mem` scripts
are not imported as current operational tools.

## Early console is different from a registered serial port

On the measured Baikal B1 setup, the useful early-console address was
`0xC890E000`, derived from the glue function's actual BAR and UART offset;
the working argument used `uart8250,mmio32` and 115200n8. UART registers have
four-byte spacing and require the appropriate MMIO access type. Treat the
address as that console's observed mapping, not a universal value for every
PS4 or PS5. Normal `ttyS` numbering and the physical connected UART are
separate identities.

The old driver also had incomplete serial-port registration/transmit behavior.
A 5.4 `PORT_16550A`/fixed-type experiment did not provide a qualified 6.x
solution. The active 6.18 series keeps UART-registration failure non-fatal
rather than letting it prevent the Baikal glue and its children from binding.
Do not enable the disabled port-type patch or infer a pre-kernel triple fault
solely from a silent cable. Wrong UART, early-console retirement, capture
loss and a real boot fault can have similar outward symptoms.

Legacy PC `earlyprintk=serial,ttyS0,...` targets a different I/O-port model;
it is not a substitute for the measured PS4 MMIO console. Similarly,
`console=ttyS0` alone does not prove that the Baikal physical port is usable.
Record the actual console arguments and the observed logger continuity.

## Keep historical claims attached to their kernel

The early study guide warned against `keep_bootcon` after a 5.4 USB-root
failure. Later 6.x work explicitly revised that blanket claim and used it
for diagnosis. Copying the earlier warning or its inferred bus-overload cause
as a universal rule would discard that later evidence. Preserve the exact
known boot profile and test a change as a separate variable. The same applies
to old UART-index assumptions and early theories that all post-kexec silence
must be a triple fault.

The original sources are available for their historical sequence:

https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/checkpoint/docs/study/05-uart.md

https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/checkpoint/docs/LEARNINGS.md

Their corrected takeaways are summarized here; their raw command sequences
and logs are not promoted into the new workflow.

## A useful investigation sequence

1. Pin the complete source/config/loader/initramfs/module/boot-argument set and
   verify the output checksums before a console test.
2. State one question and add observations at the smallest relevant boundary.
   Display v58's per-step lane readings resolved more than several speculative
   combined changes. A single write/readback can reject an incorrect register
   model before a larger programmer is attempted.
3. Read the compact bounded capture before searching a rolling log. Compare
   the same stage across the candidate and recovery artifact. Logger continuity
   and observed functional progress matter more than a guessed message count.
4. Keep negative results with their changed variable. A busy MSI composer,
   successful allocation, accepted stop request or completed source build
   does not prove interrupt delivery, packet traffic or DMA drain.
5. Preserve the complete recovery boot set and stop at the declared boundary.
   Old kexec/boomerang helpers are historical experiments, not evidence that
   a warm reboot resets a published device or supplies a safe MTS rollback.

For routine source verification, use native Linux x86-64 tools or the
repository's OrbStack helper on macOS ARM64. `make check` runs entirely on
the host and needs no hardware session. Curated findings can be published;
raw logs need review for console identifiers, addresses, keys and credentials.
