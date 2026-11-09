/* Exercise the real LCD upload ownership/error paths without changing pixels. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static void check(int ok, const char *what)
{
    if (!ok) { perror(what); exit(1); }
    printf("PASS %s\n", what);
}
int main(void)
{
    int first = open("/dev/m3100-lcd", O_WRONLY);
    int second = open("/dev/m3100-lcd", O_WRONLY);
    char *pixels = calloc(1, 230401);
    check(first >= 0 && second >= 0 && pixels != NULL, "open and allocate");
    errno = 0;
    check(write(first, "invalid\n", 8) == -1 && errno == EINVAL, "reject invalid command");
    check(write(first, "frame\n", 6) == 6, "start upload");
    check(write(first, pixels, 1) == 1, "accept partial upload");
    errno = 0;
    check(write(second, "off\n", 4) == -1 && errno == EBUSY, "exclude second writer");
    errno = 0;
    check(write(first, pixels, 230400) == -1 && errno == EMSGSIZE, "reject upload overflow");
    check(close(first) == 0, "discard incomplete upload");
    check(write(second, "frame\n", 6) == 6, "release ownership on close");
    check(close(second) == 0, "discard second incomplete upload");
    free(pixels);
    return 0;
}
