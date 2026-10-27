## Build-time shim for the headless binary: see curl_stub.c. native.py passes
## --dynlibOverride:libcurl --import:<this file>, so this module links the stub
## instead of the program loading libcurl.so at startup.
{.compile: "curl_stub.c".}
