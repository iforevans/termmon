PKG := $(shell pkg-config --exists ncursesw 2>/dev/null && echo ncursesw || echo ncurses)
PKG_CFLAGS := $(shell pkg-config --cflags $(PKG) 2>/dev/null)
PKG_LIBS := $(shell pkg-config --libs $(PKG) 2>/dev/null || echo -lncursesw)

CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra $(PKG_CFLAGS)
LDLIBS := $(PKG_LIBS) -lpthread -lm

SRC := $(wildcard src/*.c)
OBJ := $(SRC:.c=.o)
BIN := termmon

all: $(BIN)

$(BIN): $(OBJ)
	$(CC) $(CFLAGS) -o $@ $(OBJ) $(LDLIBS)

%.o: %.c src/termmon.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJ) $(BIN) tests/test_layout

test: tests/test_layout $(BIN)
	./tests/test_layout
	python3 tests/test_golden.py
	python3 tests/test_pty_layout.py
	python3 tests/test_pty_layout.py --bin ./$(BIN)

tests/test_layout: tests/test_layout.c src/layout.o
	$(CC) $(CFLAGS) -Isrc -o $@ tests/test_layout.c src/layout.o -lm

install: $(BIN)
	@test -f $(HOME)/bin/termmon -a ! -f $(HOME)/bin/termmon.py.bak \
	  && cp -p $(HOME)/bin/termmon $(HOME)/bin/termmon.py.bak || true
	install -m 755 $(BIN) $(HOME)/bin/termmon

uninstall:
	rm -f $(HOME)/bin/termmon
	@test -f $(HOME)/bin/termmon.py.bak \
	  && mv $(HOME)/bin/termmon.py.bak $(HOME)/bin/termmon || true

.PHONY: all clean test install uninstall
