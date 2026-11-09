/*
 * OdyCAT Engine — the shared-memory regions and the instance lock.
 *
 * The regions are plain files on tmpfs in the run directory, by default
 * /run/odycat (ODYCAT_RUN_DIR overrides it): a directory only its owner can
 * create files in, so no other user can plant a region or hold the lock
 * before the engine starts.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>

#include "layout.h"
#include "sha256.h"
#include "shm.h"
#include "util.h"

#ifndef ODYCAT_VERSION
#define ODYCAT_VERSION "unknown"
#endif
#ifndef ODYCAT_GIT
#define ODYCAT_GIT "unknown"
#endif
#ifndef ODYCAT_SUPPORT_END
#define ODYCAT_SUPPORT_END 0        /* YYYYMM; set per release by the build */
#endif

#define RUN_DIR_DEFAULT  "/run/odycat"
#define TMPFS_MAGIC_ID   0x01021994

static char  g_run_dir[256]         = "";
static char  g_data_path[512]       = "";
static char  g_config_path[512]     = "";
static char  g_trace_path[512]      = "";
static char  g_lock_path[512]       = "";
static int   g_lock_fd              = -1;
static bool  g_data_created         = false;  /* unlink on die() only if ours */
static char  g_shm_group[64]        = "";
static gid_t g_shm_gid              = (gid_t)-1;

/* ------------------------------------------------------------------ */
/*  Paths                                                              */
/* ------------------------------------------------------------------ */

/* The bus name becomes part of file names: letters, digits, '_',
 * '.', '-' only, so it can never point outside the run directory. */
static void check_bus_name(const char *bus) {
    if (!bus[0] || !strcmp(bus, ".") || !strcmp(bus, ".."))
        die("bus name '%s' is not valid", bus);
    for (const char *c = bus; *c; c++)
        if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') ||
              (*c >= '0' && *c <= '9') || *c == '_' || *c == '.' || *c == '-'))
            die("bus name '%s' is not valid: use letters, digits, '_', '.' "
                "and '-' only", bus);
}

void shm_set_paths(const char *bus) {
    check_bus_name(bus);
    const char *dir = getenv("ODYCAT_RUN_DIR");
    if (!dir || !dir[0])
        dir = RUN_DIR_DEFAULT;
    if (dir[0] != '/')
        die("ODYCAT_RUN_DIR must be an absolute path, not '%s'", dir);
    if (strlen(dir) >= sizeof(g_run_dir))
        die("ODYCAT_RUN_DIR is too long");
    snprintf(g_run_dir, sizeof(g_run_dir), "%s", dir);
    snprintf(g_data_path, sizeof(g_data_path), "%s/%s_shared", g_run_dir, bus);
    snprintf(g_config_path, sizeof(g_config_path), "%s/%s_config", g_run_dir, bus);
    snprintf(g_trace_path, sizeof(g_trace_path), "%s/%s_trace", g_run_dir, bus);
    snprintf(g_lock_path, sizeof(g_lock_path), "%s/%s.lock", g_run_dir, bus);
}

const char *shm_run_dir(void)     { return g_run_dir; }
const char *shm_data_path(void)   { return g_data_path; }
const char *shm_config_path(void) { return g_config_path; }
const char *shm_trace_path(void)  { return g_trace_path; }

/* ------------------------------------------------------------------ */
/*  Group                                                              */
/* ------------------------------------------------------------------ */

/* The regions are only accessible to their owner and the shm group
 * (default "odycat"), independent of the umask. */
void shm_resolve_group(const char *group) {
    snprintf(g_shm_group, sizeof(g_shm_group), "%s", group);
    struct group *gr = getgrnam(g_shm_group);
    if (!gr) {
        struct passwd *pw = getpwuid(geteuid());
        die("group '%s' does not exist. OdyCAT shared memory is only accessible "
            "to that group. Create it and add the users that run the engine or "
            "Python scripts:\n  sudo groupadd %s\n  sudo usermod -aG %s %s\n"
            "then log in again. Set \"shm_group\" in the config to use another group.",
            g_shm_group, g_shm_group, g_shm_group, pw ? pw->pw_name : "<user>");
    }
    g_shm_gid = gr->gr_gid;
}

/* ------------------------------------------------------------------ */
/*  Run directory                                                      */
/* ------------------------------------------------------------------ */

/* Create the run directory if it is missing (0750, owner and shm group),
 * then check it: a real directory (not a link), on tmpfs, owned by root or
 * by the user the engine runs as, writable by its owner only, and writable
 * by the engine. */
void shm_prepare_run_dir(void) {
    struct stat st;
    if (lstat(g_run_dir, &st) < 0) {
        if (errno != ENOENT)
            die("cannot use run directory '%s': %s", g_run_dir, strerror(errno));
        if (mkdir(g_run_dir, 0750) < 0)
            die("cannot create run directory '%s': %s. It is created by the systemd "
                "unit, or by the engine when it runs as root. Without sudo, set "
                "ODYCAT_RUN_DIR to a directory of yours, e.g. "
                "ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat", g_run_dir, strerror(errno));
        if (chown(g_run_dir, (uid_t)-1, g_shm_gid) < 0 || chmod(g_run_dir, 0750) < 0)
            die("cannot set owner group and mode of run directory '%s': %s",
                g_run_dir, strerror(errno));
        if (lstat(g_run_dir, &st) < 0)
            die("cannot use run directory '%s': %s", g_run_dir, strerror(errno));
    }
    if (!S_ISDIR(st.st_mode))
        die("run directory '%s' is not a directory", g_run_dir);
    if (st.st_uid != 0 && st.st_uid != geteuid())
        die("run directory '%s' belongs to uid %u, not to root or to this user",
            g_run_dir, (unsigned)st.st_uid);
    if (st.st_mode & (S_IWGRP | S_IWOTH))
        die("run directory '%s' is writable by its group or by others (mode %04o): "
            "only its owner may create files there. Fix it with: chmod 0750 %s",
            g_run_dir, (unsigned)(st.st_mode & 07777), g_run_dir);
    if (access(g_run_dir, W_OK) < 0)
        die("the engine cannot create files in run directory '%s' (owned by uid %u): "
            "start it with sudo or as the systemd service, or set ODYCAT_RUN_DIR to "
            "a directory of yours, e.g. ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat",
            g_run_dir, (unsigned)st.st_uid);
    struct statfs fs;
    if (statfs(g_run_dir, &fs) == 0 && (unsigned long)fs.f_type != TMPFS_MAGIC_ID)
        die("run directory '%s' is not on tmpfs: the shared memory would be "
            "written to disk. Use a directory under /run.", g_run_dir);
}

/* ------------------------------------------------------------------ */
/*  Instance lock                                                      */
/* ------------------------------------------------------------------ */

void shm_release_instance_lock(void) {
    if (g_lock_fd >= 0) {
        flock(g_lock_fd, LOCK_UN);
        close(g_lock_fd);
        g_lock_fd = -1;
    }
}

/* One engine per bus. The lock file stays in the run directory
 * between runs; a lock left by another trusted owner (root, the service
 * user) is opened read-only, which is enough for flock. */
void shm_acquire_instance_lock(const char *bus) {
    on_die(shm_release_instance_lock);
    bool writable = true;
    g_lock_fd = open(g_lock_path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0640);
    if (g_lock_fd < 0 && errno == EACCES) {
        writable = false;
        g_lock_fd = open(g_lock_path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    }
    if (g_lock_fd < 0)
        die("cannot open instance lock '%s': %s", g_lock_path, strerror(errno));

    if (flock(g_lock_fd, LOCK_EX | LOCK_NB) < 0) {
        char holder[64] = {0};
        lseek(g_lock_fd, 0, SEEK_SET);
        ssize_t n = read(g_lock_fd, holder, sizeof(holder) - 1);
        if (n > 0) {
            holder[strcspn(holder, "\n")] = '\0';
            die("another OdyCAT instance is already running for bus '%s' "
                "(pid %s). Stop it first.", bus, holder);
        }
        die("another OdyCAT instance is already running for bus '%s'. Stop it first.",
            bus);
    }

    if (writable) {
        /* The shm group may open it: an engine run by hand as root, then
         * the service user (or the other way round), share the same lock. */
        if (fchown(g_lock_fd, (uid_t)-1, g_shm_gid) < 0 || fchmod(g_lock_fd, 0640) < 0)
            fprintf(stderr, "WARNING: cannot give lock file '%s' to group '%s': %s\n",
                    g_lock_path, g_shm_group, strerror(errno));
        if (ftruncate(g_lock_fd, 0) < 0)
            die("cannot truncate lock file '%s': %s", g_lock_path, strerror(errno));
        dprintf(g_lock_fd, "%ld\n", (long)getpid());
        fsync(g_lock_fd);
    }
}

/* ------------------------------------------------------------------ */
/*  Regions                                                            */
/* ------------------------------------------------------------------ */

static void restrict_region(int fd, const char *path, mode_t mode) {
    if (fchown(fd, (uid_t)-1, g_shm_gid) < 0)
        die("cannot give '%s' to group '%s': %s. The engine must run as root "
            "or as a member of that group (log in again after usermod).",
            path, g_shm_group, strerror(errno));
    if (fchmod(fd, mode) < 0)
        die("cannot set mode %04o on '%s': %s", (unsigned)mode, path, strerror(errno));
}

int shm_create_region(const char *path, mode_t mode, bool *created) {
    for (int attempt = 0; attempt < 2; attempt++) {
        int fd = open(path, O_CREAT | O_EXCL | O_RDWR | O_NOFOLLOW | O_CLOEXEC, mode);
        if (fd >= 0) {
            if (created)
                *created = true;
            restrict_region(fd, path, mode);
            return fd;
        }
        if (errno != EEXIST)
            die("cannot create '%s': %s", path, strerror(errno));
        /* We hold the instance lock, so no engine is using it: a region
         * left by a run that ended without cleaning up. Only the run
         * directory's owner or root can have created it; anything else is
         * refused rather than reused. */
        struct stat st;
        if (lstat(path, &st) < 0)
            die("cannot check '%s': %s", path, strerror(errno));
        if (!S_ISREG(st.st_mode) || (st.st_uid != 0 && st.st_uid != geteuid()))
            die("'%s' already exists and was not left by an OdyCAT engine (owner uid "
                "%u). Remove it after checking where it comes from.",
                path, (unsigned)st.st_uid);
        if (unlink(path) < 0)
            die("cannot remove the stale region '%s': %s", path, strerror(errno));
    }
    die("cannot create '%s': it reappeared after removing it", path);
}

/* die(): don't leave a stale region for Python to attach to. */
static void unlink_data_and_config(void) {
    if (g_data_created) {
        unlink(g_data_path);
        unlink(g_config_path);
    }
}

uint8_t *shm_create_data(const odycat_config_t *cfg) {
    on_die(unlink_data_and_config);
    size_t size = cfg->shm_size;
    int fd = shm_create_region(g_data_path, 0660, &g_data_created);
    if (ftruncate(fd, (off_t)size) < 0)
        die("ftruncate shm to %zu bytes failed: %s", size, strerror(errno));

    uint8_t *base = mmap(NULL, size, PROT_READ | PROT_WRITE,
                         MAP_SHARED | MAP_POPULATE, fd, 0);
    if (base == MAP_FAILED)
        die("mmap shm failed: %s", strerror(errno));
    close(fd);
    memset(base, 0, size);

    odycat_header_t *h = (odycat_header_t *)base;
    h->layout_major = ODYCAT_LAYOUT_MAJOR;
    h->layout_minor = ODYCAT_LAYOUT_MINOR;
    h->header_size  = SHM_HEADER_SIZE;
    h->cycle_us     = (uint32_t)cfg->cycle_us;

    /* Output buffers and sync groups: fixed by the config, so scripts can
     * write outputs from the start (all buffers zero, epoch 0) */
    h->out_table_offset = (uint32_t)cfg->out_table_offset;
    h->group_count      = (uint32_t)cfg->group_count;
    h->groups_offset    = (uint32_t)cfg->groups_offset;
    h->busy_offset      = (uint32_t)cfg->busy_offset;
    odycat_out_entry_t *out = (odycat_out_entry_t *)(base + cfg->out_table_offset);
    for (int i = 0; i < cfg->num_slaves; i++) {
        const slave_cfg_t *s = &cfg->slaves[i];
        out[i].group = s->out_group < 0 ? ODYCAT_NO_GROUP : (uint16_t)s->out_group;
        if (s->out_group < 0)
            continue;
        out[i].member     = (uint8_t)s->out_member;
        out[i].buf_offset = (uint32_t)s->out_buf_offset;
        out[i].buf_stride = (uint32_t)s->out_buf_stride;
    }
    atomic_thread_fence(memory_order_release);
    h->magic        = ODYCAT_DATA_MAGIC;      /* last: readers check it first */
    return base;
}

const char *shm_engine_version(void) { return ODYCAT_VERSION; }
const char *shm_engine_git(void)     { return ODYCAT_GIT; }

/* "security fixes until YYYY-MM", or that a development build has no
 * support period (SUPPORT_END in src/odycat/_version.py). */
const char *shm_engine_support(void) {
    static char text[48];
    unsigned end = ODYCAT_SUPPORT_END;
    if (end)
        snprintf(text, sizeof(text), "security fixes until %04u-%02u", end / 100, end % 100);
    else
        snprintf(text, sizeof(text), "no support period: development build");
    return text;
}

/* The config region: a header saying which engine loaded which config
 * (version, git, SHA-256 of the file, pid, start time, end of support, a
 * random instance id), then the config file as loaded. The engine does not
 * keep it mapped. */
void shm_publish_config(odycat_config_t *cfg) {
    /* 0640: only the engine writes it; scripts read the config from it, so
     * a group member must not be able to change what the others decode */
    int fd = shm_create_region(g_config_path, 0640, NULL);
    uint32_t json_len = (uint32_t)cfg->text_len;
    size_t size = CONFIG_HEADER_SIZE + json_len;
    if (ftruncate(fd, (off_t)size) < 0)
        die("ftruncate config shm failed: %s", strerror(errno));
    uint8_t *base = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED)
        die("mmap config shm failed: %s", strerror(errno));

    odycat_config_header_t h;
    memset(&h, 0, sizeof(h));
    h.layout_major = ODYCAT_LAYOUT_MAJOR;
    h.layout_minor = ODYCAT_LAYOUT_MINOR;
    h.header_size  = CONFIG_HEADER_SIZE;
    h.json_len     = json_len;
    snprintf(h.engine_version, sizeof(h.engine_version), "%s", ODYCAT_VERSION);
    snprintf(h.engine_git, sizeof(h.engine_git), "%s", ODYCAT_GIT);
    sha256(cfg->text, json_len, h.config_sha256);
    h.engine_pid        = (uint32_t)getpid();
    h.support_end       = ODYCAT_SUPPORT_END;
    h.start_realtime_ns = realtime_ns();
    if (getrandom(h.instance_id, sizeof(h.instance_id), 0) != (ssize_t)sizeof(h.instance_id))
        die("getrandom failed: %s", strerror(errno));

    memcpy(base + CONFIG_HEADER_SIZE, cfg->text, json_len);
    memcpy(base, &h, sizeof(h));
    atomic_thread_fence(memory_order_release);
    ((odycat_config_header_t *)base)->magic = ODYCAT_CONFIG_MAGIC;   /* last */
    munmap(base, size);
    close(fd);

    VPRINTF("OdyCAT: config sha256 ");
    for (size_t i = 0; i < sizeof(h.config_sha256); i++)
        VPRINTF("%02x", h.config_sha256[i]);
    VPRINTF("\n");
    free(cfg->text);
    cfg->text = NULL;
}

void shm_publish_bus_info(odycat_header_t *shm, odycat_slave_status_t *status,
                          const odycat_config_t *cfg) {
    shm->expected_wkc = layout_expected_wkc(cfg);
    shm->bus_state = AL_OP;
    for (int i = 0; i < cfg->num_slaves; i++) {
        const slave_cfg_t *s = &cfg->slaves[i];
        status[i].ring_pos     = (uint16_t)s->ring_pos;
        status[i].block_offset = (uint32_t)s->shm_offset;   /* Python checks its layout */
        status[i].in_bytes     = (uint16_t)s->tx.size;
        status[i].out_bytes    = (uint16_t)s->rx.size;
    }
    shm->status_count  = (uint32_t)cfg->num_slaves;
    shm->status_offset = (uint32_t)cfg->status_offset;
    VPRINTF("OdyCAT: expected WKC = %u\n", shm->expected_wkc);
}

void shm_remove(uint8_t *base, size_t size) {
    munmap(base, size);
    unlink(g_data_path);
    unlink(g_config_path);
}
