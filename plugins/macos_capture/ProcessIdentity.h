#ifndef GAMEQA_PROCESS_IDENTITY_H
#define GAMEQA_PROCESS_IDENTITY_H

#include <libproc.h>
#include <stdint.h>
#include <sys/proc_info.h>

/* Launch Services registration is optional for command-line window owners. */
static inline int gameqa_process_identity(pid_t pid, uint64_t *seconds,
                                         uint64_t *microseconds, char *path,
                                         uint32_t path_size) {
    struct proc_bsdinfo info = {0};
    int count = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info));
    if (count != sizeof(info) || info.pbi_start_tvsec == 0 ||
        proc_pidpath(pid, path, path_size) <= 0) {
        return 0;
    }
    *seconds = info.pbi_start_tvsec;
    *microseconds = info.pbi_start_tvusec;
    return 1;
}

#endif
