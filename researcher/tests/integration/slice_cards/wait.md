
0. First wait for the test harness. Run this command:
```sh
for i in $(seq 1 50); do [ -f .scratch/go ] && break; sleep 2; done; if [ -f .scratch/go ]; then echo GO; else echo WAITING; fi
```
If it prints WAITING, run the same command again. Repeat until it prints GO. Then go on with step 1.
