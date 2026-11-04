TARGET ?= 6.18-ps4-baikal

.PHONY: all build patches-only check check-diagnostics help
all: build
build:
	./build.sh --target $(TARGET)
patches-only:
	./build.sh --target $(TARGET) --patches-only
check:
	python3 userspace/mesa/check-source.py
	python3 scripts/check-provenance
	python3 -m unittest discover -s tests -p '*_test.py'
	bash -n build.sh scripts/build-kernel-orbstack scripts/ci/package-release
check-diagnostics:
	python3 scripts/check-ps4-diagnostics
help:
	./build.sh --help
