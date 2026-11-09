.PHONY: plan build test
plan:
	python3 build.py --bundle "$(BUNDLE)" --output "$(OUTPUT)"
build:
	python3 build.py --bundle "$(BUNDLE)" --output "$(OUTPUT)" --apply
test:
	TMPDIR=/var/tmp python3 -m unittest discover -s tests -v
