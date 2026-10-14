# FineTune for Linux — audio engine
CC ?= cc
CFLAGS ?= -O2 -g
CFLAGS += -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers $(shell pkg-config --cflags libpipewire-0.3)
LDLIBS += $(shell pkg-config --libs libpipewire-0.3) -lm
PREFIX ?= /usr/local

all: finetune-engine

finetune-engine: finetune-engine.c dsp.h
	$(CC) $(CFLAGS) -o $@ finetune-engine.c $(LDLIBS)

install: finetune-engine
	install -Dm755 finetune-engine $(DESTDIR)$(PREFIX)/lib/finetune/finetune-engine

test: test_dsp.c dsp.h
	$(CC) -std=gnu11 -O2 -Wall -o test_dsp test_dsp.c -lm && ./test_dsp

clean:
	rm -f finetune-engine test_dsp

.PHONY: all install clean test
