/*
 * libcurl stand-in for the Pudge Wars Basic headless binary.
 *
 * Why: the headless build imports polyworld/llms.nim, which imports curly, which binds
 * libcurl through Nim's {.dynlib.}. Nim loads that library when the program starts, so the
 * binary dies with "could not load: libcurl.so(|.4)" on a host without libcurl (the hosted
 * research sandbox does not guarantee it). native.py builds with
 * --dynlibOverride:libcurl --import:curl_stub.nim, which links these symbols instead.
 *
 * Effect: curly's import-time setup (curl_global_init, curl_version_info) succeeds. Every
 * other entry point is only reached when a policy sends an LLM request through a configured
 * endpoint, which local episodes never have; if one is reached anyway the binary aborts with
 * a message instead of simulating something different. Simulation is unchanged: the stubbed
 * build reproduces the league host's state hashes (FADD3447 for the seed-7 3v3 sample,
 * A59D8019 for the seed-2026 duel) on macOS arm64 and Linux arm64.
 */
#include <stdio.h>
#include <stdlib.h>

typedef struct {
  int age; const char *version; int version_num; const char *host; int features;
  const char *ssl_version; int ssl_version_num; const char *libz_version;
  const char *const *protocols; const char *ares; int ares_num; const char *libidn;
  int iconv_ver_num;
} stub_version_info;

static const char *const stub_protocols[] = { NULL };
static stub_version_info stub_info = { 0, "stub", 0, "stub", 1 << 7, "", 0, "", stub_protocols, "", 0, "", 0 };

int curl_global_init(long flags) { (void)flags; return 0; }
void *curl_version_info(int age) { (void)age; return &stub_info; }
const char *curl_easy_strerror(int code) { (void)code; return "libcurl is not linked into this build"; }
const char *curl_multi_strerror(int code) { (void)code; return "libcurl is not linked into this build"; }

static void unavailable(const char *name) {
  fprintf(stderr, "pudge_headless: %s called, but this build has no libcurl (policy LLM requests are unavailable locally)\n", name);
  abort();
}
#define UNAVAILABLE(name) void *name(void) { unavailable(#name); return NULL; }
UNAVAILABLE(curl_easy_cleanup)
UNAVAILABLE(curl_easy_getinfo)
UNAVAILABLE(curl_easy_init)
UNAVAILABLE(curl_easy_reset)
UNAVAILABLE(curl_easy_setopt)
UNAVAILABLE(curl_multi_add_handle)
UNAVAILABLE(curl_multi_cleanup)
UNAVAILABLE(curl_multi_info_read)
UNAVAILABLE(curl_multi_init)
UNAVAILABLE(curl_multi_perform)
UNAVAILABLE(curl_multi_poll)
UNAVAILABLE(curl_multi_remove_handle)
UNAVAILABLE(curl_multi_setopt)
