# Chromium 151 GPU TSYNC source candidate

This is a local, default-off source candidate for the GPU sandbox startup failure
observed in diagnostics EXP-20261105-012. It is not an installed fix, a Chromium
integration test, hardware acceptance, or a change to the UVD boot lifecycle.
The existing browser/provider, console files and experiment evidence stay intact.

The experiment selected `VaapiVideoDecoder` through the end of its 900-frame
clip, but GPU sandbox initialization returned early because driver threads
already existed. The recorded GPU process had `Seccomp=0`. The two dropped
frames are a separate observation and are not explained or fixed by this patch.

## Exact source and change

- Chromium tag: `151.0.7922.169`.
- Upstream file: `content/gpu/gpu_main.cc`.
- Source URL: https://raw.githubusercontent.com/chromium/chromium/151.0.7922.169/content/gpu/gpu_main.cc
- Original file SHA256: `92069dde535c687534758fca6013ac15da9f19629ef1c71315cf64aa82254fd3`.
- Patch SHA256: `08ca1d2c5b3660c6a0de7acff3928bc00bd10bd2f4f7f9c63c603f2cef2758dc`.
- Patched file SHA256: `281d523a13883653eef955b9edf3143373034566e908c7b0fc3bfec75408b1cf`.

Only the exact process environment value `PSX_CHROMIUM_GPU_TSYNC=1` enables the
candidate, and only in the Linux GPU startup path. The variable is deliberately
not an automatic AMD/PS4 detector. Unset or other values retain upstream
behavior. A future reviewed launcher must set it for that single browser trial;
do not install a global environment setting.

The patch sets Chromium's existing `allow_threads_during_sandbox_init` option.
When workers already exist, Chromium installs the same GPU seccomp policy using
`SECCOMP_FILTER_FLAG_TSYNC`. Missing TSYNC or filter installation failure is fatal
in upstream code. The candidate additionally requires both a successful
`InitializeSandbox()` return and `seccomp_bpf_started()` before continuing.
It does not change namespace settings, broker permissions, syscall rules,
open-directory checks, decoder policy, or sandbox-disabling flags.

Relevant exact-tag implementation:

- https://raw.githubusercontent.com/chromium/chromium/151.0.7922.169/sandbox/policy/linux/sandbox_linux.h
- https://raw.githubusercontent.com/chromium/chromium/151.0.7922.169/sandbox/policy/linux/sandbox_linux.cc
- https://raw.githubusercontent.com/chromium/chromium/151.0.7922.169/sandbox/linux/seccomp-bpf/sandbox_bpf.cc

The matched Mesa 26.2.5 candidate has manifest SHA256
`687af5a366d4bba5af1f72cf5b1a57c4e09d1cfef58257a898ee34da2c7e3964` and loaded
driver SHA256 `16d87fdff0011054eeba34e2dff9067519d8756de788c4970cecf4a7038ce4a3`.
Its amdgpu winsys creates a command-submission worker, and radeonsi creates
shader-compiler queues; disabling GL threading alone does not remove these.
`--gpu-sandbox-start-early` is a separate upstream ordering option, with different
library-access requirements. This candidate does not add or depend on it.

## Bounded host check

Supply the one original upstream file locally. The checker performs no downloads,
browser launch, console access or device operation. It copies the input into a
temporary directory, rejects a changed source fixture, applies the patch with
zero fuzz, verifies the exact result, then compiles and runs a small native C
test. Run on Linux x86-64 with Python 3, `patch`, and a C compiler:

```sh
env TMPDIR=/var/tmp python3 experiments/ps4-browser-uvd/check.py \
  --upstream-file /path/to/original/gpu_main.cc
```

The native test forks isolated children. In the normal case, a harmless syscall
is initially allowed in both the main thread and a worker created before filter
installation. The main thread installs a real TSYNC filter; the same syscall
must then return `EACCES` in both threads, with seccomp filter mode and
`NoNewPrivs` active. A separate child injects absent capability, and another uses
invalid flags to obtain a real kernel rejection. Neither failure may reach its
continuation marker. The parent remains unaffected. Each child has a five-second
alarm; compilation and execution also have outer timeouts.

These checks establish patch identity/application and the host Linux mechanism.
They do not compile Chromium, exercise Chromium's broker/namespace initialization,
prove the PS4 kernel's TSYNC behavior, or establish successful decoding after the
patch. The test filter is intentionally tiny and is not a replacement GPU policy.

## Before integration

A full Chromium source build and new immutable provider identity are required.
Do not patch the shipped executable or relabel the existing provider. Review the
Chromium sandbox integration, including broker startup with existing workers and
open-directory invariants, and retain its failure checks. A rebuilt integration
test must verify the real GPU and decoder threads have active filters before
playback, with no process restart or sandbox failure, then independently verify
the selected VAAPI backend and completion. Any existing UVD session, intent,
one-attempt, fault, and final-active-action rules still apply.
