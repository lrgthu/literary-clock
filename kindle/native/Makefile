CC ?= cc
ARM_CC ?= arm-kindlehf-linux-gnueabihf-gcc
ARM_STRIP ?= $(patsubst %gcc,%strip,$(ARM_CC))

CPPFLAGS := -Iinclude
WARNINGS := -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 $(WARNINGS)
LDFLAGS ?=

SOURCES := $(wildcard src/*.c)
CORE_SOURCES := $(filter-out src/main.c,$(SOURCES))
HOST_OBJECTS := $(patsubst src/%.c,build/host/%.o,$(SOURCES))
TEST_OBJECTS := $(patsubst src/%.c,build/test/%.o,$(CORE_SOURCES)) build/test/test_core.o

HOST_BINARY := build/host/litclock-native
TEST_BINARY := build/test/test-core
SANITIZER_BINARY := build/sanitize/litclock-native
SANITIZER_TEST := build/sanitize/test-core
ARM_BINARY := build/armv7/litclock-native

.PHONY: all host test sanitize armv7 clean

all: host

host: $(HOST_BINARY)

$(HOST_BINARY): $(HOST_OBJECTS)
	$(CC) $(HOST_OBJECTS) $(LDFLAGS) -o $@

build/host/%.o: src/%.c include/litclock.h
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

test: $(TEST_BINARY)
	$(TEST_BINARY)

$(TEST_BINARY): $(TEST_OBJECTS)
	$(CC) $(TEST_OBJECTS) $(LDFLAGS) -o $@

build/test/%.o: src/%.c include/litclock.h
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

build/test/test_core.o: tests/test_core.c include/litclock.h
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

sanitize:
	@mkdir -p build/sanitize
	$(CC) $(CPPFLAGS) -std=c11 $(WARNINGS) -O1 -g -fno-omit-frame-pointer \
		-fsanitize=address,undefined $(SOURCES) -o $(SANITIZER_BINARY)
	$(CC) $(CPPFLAGS) -std=c11 $(WARNINGS) -O1 -g -fno-omit-frame-pointer \
		-fsanitize=address,undefined $(CORE_SOURCES) tests/test_core.c -o $(SANITIZER_TEST)
	$(SANITIZER_TEST)

ARM_CFLAGS ?= -Os -g0 -march=armv7-a -mtune=cortex-a9 -mfpu=neon -mfloat-abi=hard -mthumb
ARM_LDFLAGS ?= -static

armv7:
	@mkdir -p build/armv7
	@command -v $(ARM_CC) >/dev/null || { \
		echo "missing KindleHF compiler: $(ARM_CC)" >&2; \
		echo "install the official koxtoolchain KindleHF compiler or set ARM_CC" >&2; \
		exit 127; \
	}
	$(ARM_CC) $(CPPFLAGS) -std=c11 $(WARNINGS) $(ARM_CFLAGS) $(SOURCES) \
		$(ARM_LDFLAGS) -o $(ARM_BINARY)
	$(ARM_STRIP) --strip-all $(ARM_BINARY)

clean:
	rm -rf build
