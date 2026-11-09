# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# OdyCAT engine. The only place that knows how to compile it: CI and the
# top-level Makefile call this one. The Python wheel does not contain it.
#
#   make                 ./odycat
#   make OUT=path        build somewhere else
#   make LTO=            without link-time optimisation
#   make deps            only check the build dependencies
#   sudo make install    /usr/local/bin/odycat, root-owned (PREFIX=, DESTDIR=)
#   sudo make uninstall
#   sudo make install-service    also the systemd service: odycat@.service, the
#                                odycat system user, the polkit rule, /etc/odycat
#   sudo make uninstall-service  the unit and the polkit rule (user and configs stay)
#
# Build as your user first, then install with sudo: only root can then
# replace the program that `sudo odycat` runs.
#
# The sources are compiled in one call with link-time optimisation (-flto), so
# the compiler sees the whole engine at once and can inline across files (the
# trace and copy functions called every cycle), as it did with one source file.
#
# The engine runs as root and parses a config file: it is built hardened
# (stack protector, fortify, PIE, full RELRO).

ifeq ($(origin CC),default)
CC       := gcc
endif
OUT      ?= odycat
IGH      ?= /usr/local
PREFIX   ?= /usr/local
BINDIR   ?= $(PREFIX)/bin
SYSTEMDDIR  ?= /etc/systemd/system
SYSUSERSDIR ?= /etc/sysusers.d
POLKITDIR   ?= /etc/polkit-1/rules.d
CONFDIR     ?= /etc/odycat
PKG      := ../packaging
CPPFLAGS += -I$(IGH)/include -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2
LTO      ?= -flto=auto
# Version from setup.py (one place for it), git describe of the tree, and the
# end of the support period (YYYYMM, 0 = not set): published by the engine in
# its config region and printed at startup and by --version.
VERSION  ?= $(shell sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' ../src/odycat/_version.py)
GIT      ?= $(shell git -C .. describe --always --dirty --abbrev=12 2>/dev/null || echo unknown)
SUPPORT_END ?= $(shell sed -n 's/^SUPPORT_END = \([0-9]*\).*/\1/p' ../src/odycat/_version.py)
CPPFLAGS += -DODYCAT_VERSION='"$(VERSION)"' -DODYCAT_GIT='"$(GIT)"' -DODYCAT_SUPPORT_END=$(SUPPORT_END)
CFLAGS   ?= -O2
CFLAGS   += -std=c11 -Wall -Wextra -fstack-protector-strong -fPIE $(LTO)
LDFLAGS  += $(LTO) -pie -Wl,-z,relro,-z,now -L$(IGH)/lib \
            -Wl,-rpath,$(IGH)/lib -Wl,-rpath,$(IGH)/lib64
LDLIBS   += -lethercat -lcjson -lrt -lpthread

SRCS     := main.c config.c layout.c shm.c trace.c igh.c rt.c util.c sha256.c
HDRS     := $(wildcard *.h)

$(OUT): $(SRCS) $(HDRS) | deps
	@mkdir -p $(dir $(OUT))
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SRCS) -o $@ $(LDFLAGS) $(LDLIBS)

# The config parser alone, without IgH (tests/config_dump.c): what the
# engine understands of a config, as JSON. For the unit tests and the fuzzer;
# needs only a compiler and libcjson-dev.
CONFIG_DUMP ?= tests/config_dump
config-dump: $(CONFIG_DUMP)
$(CONFIG_DUMP): tests/config_dump.c config.c layout.c util.c $(HDRS)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/config_dump.c config.c layout.c util.c -o $@ -lcjson

# Each check names the package to install, instead of a compiler error.
# The version compiled into the engine (src/odycat/_version.py)
version:
	@echo $(VERSION)

deps:
	@command -v $(CC) >/dev/null || { echo "ERROR: $(CC) not found (apt install build-essential)" >&2; exit 1; }
	@echo '#include <ecrt.h>' | $(CC) $(CPPFLAGS) -E -x c - >/dev/null 2>&1 || \
		{ echo "ERROR: ecrt.h not found: install the IgH EtherCAT Master userland (IGH=$(IGH))" >&2; exit 1; }
	@echo '#include <cjson/cJSON.h>' | $(CC) $(CPPFLAGS) -E -x c - >/dev/null 2>&1 || \
		{ echo "ERROR: cjson/cJSON.h not found: apt install libcjson-dev" >&2; exit 1; }
	@echo 'int main(void){return 0;}' | $(CC) -x c - -o /dev/null $(LDFLAGS) -lethercat 2>/dev/null || \
		{ echo "ERROR: libethercat not found in $(IGH)/lib: install the IgH EtherCAT Master userland" >&2; exit 1; }

install: $(OUT)
	install -D -m 0755 $(OUT) $(DESTDIR)$(BINDIR)/odycat

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/odycat

# The engine as a systemd service (docs/getting-started.md): the odycat@ unit
# with this BINDIR, the odycat system user (sysusers.d), the polkit rule that
# lets the odycat group start and stop it, and /etc/odycat for the configs.
install-service: install
	install -d $(DESTDIR)$(SYSTEMDDIR)
	sed 's|@BINDIR@|$(BINDIR)|' $(PKG)/systemd/odycat@.service.in > $(DESTDIR)$(SYSTEMDDIR)/odycat@.service
	chmod 0644 $(DESTDIR)$(SYSTEMDDIR)/odycat@.service
	install -D -m 0644 $(PKG)/sysusers.d/odycat.conf $(DESTDIR)$(SYSUSERSDIR)/odycat.conf
	install -D -m 0644 $(PKG)/polkit/50-odycat.rules $(DESTDIR)$(POLKITDIR)/50-odycat.rules
	install -d -m 0750 $(DESTDIR)$(CONFDIR)
ifeq ($(DESTDIR),)
	systemd-sysusers $(SYSUSERSDIR)/odycat.conf
	chgrp odycat $(CONFDIR)
	systemctl daemon-reload
endif
	@echo "Next: a config in $(CONFDIR)/<name>.json (root:odycat, 0640), then"
	@echo "      systemctl enable --now odycat@<name>"

uninstall-service:
	rm -f $(DESTDIR)$(SYSTEMDDIR)/odycat@.service $(DESTDIR)$(POLKITDIR)/50-odycat.rules

clean:
	rm -f odycat $(CONFIG_DUMP)

.PHONY: config-dump version deps install uninstall install-service uninstall-service clean
